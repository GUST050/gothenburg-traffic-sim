# LOSO, candidate-pool, picker, and warming improvement plan

**Prepared:** 2026-08-08
**Status:** WP1 root cause isolated; v8 controlled-rounding treatment passed a
targeted two-seed 134/2276 screen; production and six-seed decisions deferred
**Repository:** `/Users/gt/Documents/gs-project`
**Baseline commit:** `47e195792c753e99ff451fc096ee9e71756d0a9d`
**Primary objective:** improve defensible held-out flow recovery and reduce
seed sensitivity without leaking held observations, weakening validation, or
inventing unsupported traffic.
**Secondary objective:** decide, from evidence, whether the paused annual warm
bank should resume unchanged or be superseded by a new production build.

This document is intentionally self-contained. An actor should be able to use
it without access to the conversation that produced it.

### Implementation-review update — 2026-08-08

The six-seed `loso_pfe_meso_v5` result remains the frozen historical baseline,
but review of the first implementation found that its fold ceiling contract
was incomplete. A direction prior derived from the held sensor was correctly
removed from the picker, while the prior's reverse edge was still omitted from
the assignment field and therefore had neither the prior nor a ceiling. The
same class of omission was possible for a corridor prior involving the held
sensor. This means the earlier statement that every held-sensor-derived edge
received normal unmeasured treatment was too broad.

The validation-only implementation now releases the measured component edges,
reverse-direction-prior edges, and affected corridor-prior edges together and
labels output `loso_pfe_meso_v6` (`temporal_loso_pfe_meso_v3`). The paired
six-seed run is complete and frozen as
`validation/corrected_loso_sweep_v3_summary.json`; v5 remains preserved.
Required-edge ceiling coverage passes in all runs, but TAG still fails in all
six and 134/2276 are unchanged from v5. Station 107 additionally shows a wide
1.071–1.876 seed range. Thus v6 is evidence of a repaired validation contract,
not improved model accuracy. Production assignment and warming sources remain
unchanged; plan §13 Decision A still applies.

### Picker-publication update — 2026-08-09

The opt-in WP1 trace was implemented outside production PFE and run on the
contrasting seeds `20260808` and `20260811` for stations 107, 134 and 2276.
The immutable raw replay is
`runs/loso-picker-diagnostic-v1-20260808/`; its tracked decomposition is
`validation/loso_picker_decomposition_v1.json` (schema 3). The decisive
result is the publication boundary, not SUMO timing:

| Seed | Station | Continuous held entries | Direct rounded | Published after repair | Simulated |
|---:|---:|---:|---:|---:|---:|
| 20260808 | 107 | 7,051 | 4,622 | 6,867 | 6,863 |
| 20260808 | 134 | 2,748 | 6,102 | 7,771 | 7,771 |
| 20260808 | 2276 | 5,979 | 10,215 | 11,033 | 11,032 |
| 20260811 | 107 | 10,347 | 9,928 | 12,028 | 12,022 |
| 20260811 | 134 | 2,576 | 5,851 | 7,927 | 7,926 |
| 20260811 | 2276 | 5,972 | 10,832 | 11,911 | 11,909 |

For 134/2276, direct measurement-preserving rounding creates most of the
held-edge excess; optional structure/purpose repair adds another 818–2,076
entries. Planned and simulated totals then differ by only 0–6 vehicles, so
PFE-to-SUMO displacement is ruled out as the daily-ratio mechanism. Strict
purpose provenance does not replace route geometry. The discrete picker can
move thousands of vehicles among routes that preserve active training-sensor
incidence while changing the unobserved held edge.

V6 solved with the held assignment ceilings continuously but did not enforce
them at integer publication. V7 now enforces only those held-derived ceilings,
not every wide assignment bound, and fails closed if retained-rung integer
constraints cannot coexist. This is a validation-contract correction. A
targeted two-seed treatment run passed all publication gates but did not solve
quality: v7 ratios were 107 `1.220/2.005`, 134 `2.491/2.530`, and 2276
`2.425/2.601`. The paired v6 values were `1.071/1.876`, `2.474/2.523`, and
`2.466/2.662`. Therefore the contract fix is retained, the quality treatment
is rejected, and a six-seed v7 campaign is not justified. Evidence is frozen
in `validation/loso_integer_publication_v7_pair.json`.

The next treatment must address discrete route selection/rounding using
non-held criteria. Do not tune the existing `5×` ceiling against held counts:
the trace shows that it is usually non-binding and its two-seed enforcement
did not remove the bias. First screen any integer objective/support treatment
on these same two seeds and three stations; only a clear paired effect earns a
six-seed campaign. Validation-only code changed, `pfe.py` and production
assignment sources did not, and the annual warming content key still verifies
unchanged under §13 Decision A.

### Controlled-rounding update — 2026-08-09

The sequential measurement-repair tie-break was traced to one exact mechanism.
It prefers routes touching fewer currently active measured edges. Holding 134
or 2276 out makes routes from measured station 133 through the held branch look
one sensor more "exclusive" than routes through a still-measured branch. The
station-133 repair consequently adds a net 2,037–2,810 held entries per fold.

A validation-only joint integer projection now preserves all active rounded
measurements and the rounded interval total while minimizing route-level L1
departure from the continuous solution. It then uses the unchanged purpose,
structure and held-bound publication repairs. On the two pre-registered seeds:

| Seed | Station | v7 ratio | v8 controlled ratio | Change |
|---:|---:|---:|---:|---:|
| 20260808 | 134 | 2.491 | 1.496 | -0.995 |
| 20260811 | 134 | 2.530 | 1.590 | -0.940 |
| 20260808 | 2276 | 2.425 | 1.868 | -0.557 |
| 20260811 | 2276 | 2.601 | 1.893 | -0.708 |

All 384 targeted quarters retained zero active-sensor residual. Exact
post-publication replay shows that downstream repair adds 1,353/1,598 held
entries for 134 but removes 30/214 for 2276. The treatment is retained for
further validation because the paired effect is large and consistent, but it
is not production-ready: 2276 remains high, hourly GEH is still poor, and
station 107 has a separate integer-feasibility defect. Do not spend six seeds
yet. Treat 134's downstream constraints and 2276's route-support/objective
problem separately. Station 107's conflict is now isolated to one quarter per
seed and is feasible inside the existing 1× band, but its paired quality result
is worse/flat (`1.333/2.001` versus v7 `1.220/2.005`); reject that station's
quality variant without six seeds. Full reasoning and sources are in
`docs/plans/LOSO_CONTROLLED_ROUNDING_ROOT_CAUSE_RESEARCH_2026-08-09.md`;
compact evidence is in `validation/loso_controlled_rounding_v8_pair.json`.

---

## 1. Executive decision

Do **not** optimize the displayed LOSO score directly. Improve the route set,
junction observability, and assignment model, then measure whether the frozen
LOSO/TAG score improves on paired random seeds.

The work should proceed in this order:

1. Freeze the corrected validation contract and evidence baseline.
2. Add diagnostics that explain route support, movement support, picker use,
   assignment-ceiling activity, and time-bucket displacement.
3. Resolve the station 133/134/2276 junction as an observability problem.
4. Screen candidate-pool changes without changing the picker.
5. Screen picker/constraint changes only after the pool is understood.
6. Compare surviving variants against the baseline with the same six seeds.
7. Validate the selected variant on an untouched date.
8. Only then decide whether to change production and invalidate warming.

The current annual bank must not be deleted or relabelled. While this plan is
in diagnostic or experimental stages, keep it paused. If no production change
is accepted, resume the same bank. If a production-bound source changes, make
a new plan key and a new bank, while preserving the old bank as superseded
evidence.

---

## 2. Mandatory repository orientation

Before editing anything, read in this order:

1. `AGENTS.md`
2. the marked current blocks in `TASKS.md` and `AGENT_NOTES.md`
3. `ARCHITECTURE.md`, especially intake, observability, candidate generation,
   PFE, LOSO, and warming contracts
4. `docs/reviews/DEMAND_PIPELINE_REVIEW_2026-08-04.md`
5. `docs/reviews/PIPELINE_FAULT_AUDIT_2026-08-06.md`
6. `docs/OPEN_ISSUES_2026-08-06.md`, section 7
7. this plan

Then run:

```bash
git status --short
git diff --check
python3 tools/plan_annual_warming.py --verify
python3 tools/populate_annual_warming.py --status --state-workers 3
```

### 2.1 Dirty-worktree warning

At the time this plan was written, the worktree deliberately contained the
corrected validation work and generated reports:

- `traffic_sim/confidence/loso.py`
- `tools/validate_dmrb.py`
- `tools/run_corrected_loso_sweep.py`
- `tests/test_validate_sim.py`
- `tests/test_validate_dmrb.py`
- `web/data/loso_report.json`
- `web/data/validation.json`
- current coordination edits in `TASKS.md` and `AGENT_NOTES.md`

These changes belong to the user. Preserve them. Do not reset, overwrite, or
silently rebuild a pre-correction LOSO report.

### 2.2 Frozen baseline protocol

The frozen validation protocol is `loso_pfe_meso_v5`. Its intended guarantees
were:

- the held station is removed before structural assignment loading;
- its edges receive the same assignment ceiling as genuinely unmeasured edges;
- measurement-derived bounds, direction priors, and corridor priors involving
  the held station are excluded;
- station 107 is evaluated as one physical two-way total: simulated directions
  are summed and compared with the raw total once;
- incomplete hours are excluded, not converted to zero;
- TAG is calculated from individual complete hourly cases.

Do not change those rules inside a treatment arm. A protocol change requires a
new version and a full baseline rerun.

---

## 3. Evidence baseline

### 3.1 Corrected six-draw experiment

Local complete evidence:

`runs/loso-corrected-v2-20260808/manifest.json`

The `runs/` directory is ignored by Git, so the essential results are repeated
here for portable handoff. The run covered seeds:

`20260807, 20260808, 20260809, 20260810, 20260811, 42`

The candidate-pool SHA-256 differed on every draw, proving these were distinct
route-support realizations.

| Seed | TAG satisfactory share | Verdict |
|---:|---:|---|
| 20260807 | 71.3% | fail |
| 20260808 | 72.0% | fail |
| 20260809 | 69.2% | fail |
| 20260810 | 67.8% | fail |
| 20260811 | 62.2% | fail |
| 42 | 65.7% | fail |

Median TAG share: **68.5%**. Range: **62.2–72.0%**. Every draw is below the
strict TAG guideline of more than 85% satisfactory hourly cases.

### 3.2 Station-level baseline

| Station | Measurement | Median daily ratio | Six-draw range | Median GEH<5 share | Median TAG satisfactory share |
|---|---|---:|---:|---:|---:|
| 107 | two-way total | 1.231 | 0.991–1.875 | 62.5% | 73.0% |
| 1074 | directional | 0.804 | 0.633–0.943 | 83.3% | 95.9% |
| 1076 | directional | 0.882 | 0.685–0.995 | 75.0% | 83.3% |
| 133 | directional | 0.786 | 0.746–0.825 | 63.0% | 91.3% |
| 134 | directional | 2.472 | 2.325–2.613 | 29.2% | 39.6% |
| 2276 | directional | 2.531 | 2.433–2.662 | 14.6% | 33.3% |

Interpretation:

- 134 and 2276 have large, repeatable structural overprediction.
- 133 has smaller but repeatable underprediction.
- 107 and 1074 are strongly candidate-pool sensitive.
- TAG can rate a low-volume station satisfactory even when its daily ratio or
  GEH share appears weak, because the under-700 veh/h band permits an absolute
  difference of 100 veh/h. Report both measures; do not replace one with the
  other.

### 3.3 Junction fact that anchors this plan

Stations 133, 134, and 2276 touch node `26355153`:

- station 133: `26842525_26355153_0`, incoming;
- station 134: `26355153_96523321_0`, outgoing;
- station 2276: `26355153_91615277_0`, outgoing.

The node has four incoming and four outgoing directed legs. Only one incoming
and two outgoing legs are measured. In a LOSO fold for 134 or 2276, only two of
eight legs remain measured. Conservation cannot solve the other six unknowns.
The current `observability.junction_fixpoint` correctly refuses to fabricate an
exact derivation unless exactly one leg is unknown.

This means the repeated 134/2276 error is not fixable by algebra from the six
existing counts. It needs independent movement information, a defensible
movement prior, or a route set whose behavior is demonstrably more stable.

### 3.4 Existing pool/picker findings that must not be rediscovered

- Median distinct routes per OD pair is 1; entropy has no route-choice role for
  most OD pairs.
- Candidate support per sensor previously varied by 3.3×.
- The assignment prior's reported fit was worse than a constant
  (`R² = -5.148`).
- Thousands of wide assignment ceilings are processed, while very few bind.
- A third of ceiling slots collapse to the constant 5-vehicle floor.
- Exact route deduplication and path-size weights already exist in
  `traffic_sim/demand/pfe.py`. Do not reimplement them.
- Half tours are now labelled with `tour_partner_dropped`, but their
  directional composition bias is not corrected. Atomic deletion breaches the
  existing 75% routed-supply floor and is therefore not the default.
- The PFE uses 200 iterations with burn-in and averages feasible samples. A
  naive convergence early exit would change that estimator; first measure the
  trace.

---

## 4. Research basis and design implications

### 4.1 TAG validation

The current UK Department for Transport [TAG Unit M3.1](https://www.gov.uk/government/publications/tag-unit-m3-1-highway-assignment-modelling)
states that individual hourly link flows and turning movements should be
assessed using the volume-dependent difference criterion and GEH. Flows meeting
either measure are satisfactory, with a guideline of more than 85% of cases.

More important for this plan, TAG also says:

- outliers should be investigated even if aggregate criteria pass;
- link flows and junction turning movements test assignment quality;
- calibration and independent-validation data should be reported separately;
- analysts should review network coding and trip-matrix quality when the model
  misses the guideline;
- constraints should not be imposed merely to improve base-year validation;
- random seeds and run counts should be documented for reproducibility.

Therefore the plan treats TAG as a frozen diagnostic, investigates the
junction outliers explicitly, and prohibits feeding held counts back as
constraints.

### 4.2 SUMO count-based demand generation

SUMO's official [Routes from Observation Points](https://eclipse.dev/sumo/docs/Demand/Routes_from_Observation_Points.html)
documentation states that count matching does not define a unique demand
solution. `routeSampler` chooses repeatedly from an input route whitelist and
can consume edge counts, turn counts, and OD counts. The quality and diversity
of the input route set are therefore part of the model, not a neutral detail.

SUMO also documents that plausible alternatives can be generated with
randomized routing weights and warns that tools work badly when used for the
wrong network type. The plan tests route alternatives and movement data as
separate interventions.

SUMO's official output tooling includes
[`vehrouteCountValidation.py`](https://eclipse.dev/sumo/docs/Tools/Output.html),
which uses route exit times to measure how travel delay or delayed insertion
moves vehicles between count intervals. That directly motivates the planned
PFE-selected-versus-SUMO-entered timing diagnostic.

### 4.3 Path-flow estimation and multipath assignment

Bell, Shield, Busch, and Kruse's primary paper,
[A stochastic user equilibrium path flow estimator](https://doi.org/10.1016/S0968-090X(97)00009-0),
describes inferring path flows from urban detectors with a logit path-choice
model and count-station constraints. It supports the current separation between
route support, route-choice regularization, and measurement constraints.

Dial's primary multipath paper,
[A probabilistic multipath traffic assignment model which obviates path enumeration](https://doi.org/10.1016/0041-1647(71)90012-8),
motivates loading multiple reasonable paths instead of collapsing every OD pair
onto one shortest route. This repository already applies perturbed shortest
paths in `assignment_priors.py`; the question is whether the candidate pool
offers enough corresponding alternatives to the picker.

### 4.4 Sensor placement and information gain

Zhou and List,
[An Information-Theoretic Sensor Location Model for Traffic OD Demand Estimation Applications](https://doi.org/10.1287/trsc.1100.0319),
formulate sensor placement as maximizing expected information gain while
accounting for demand uncertainty, measurement error, and assignment
approximation. Consequently, any proposed new sensor should be ranked by how
much it reduces held-out uncertainty, not simply placed beside the worst ratio.

### 4.5 Paired stochastic experiments

Kleijnen's primary paper,
[Analyzing Simulation Experiments with Common Random Numbers](https://doi.org/10.1287/mnsc.34.1.65),
and traffic-specific work on
[common random numbers in network simulation](https://doi.org/10.1016/0191-2615(92)90031-Q)
support comparing alternatives with matched random-number streams. Treatments
must therefore use the same ordered seeds as their baseline and report paired
deltas. Unpaired “best run” comparisons are prohibited.

---

## 5. Scientific and safety contract

These rules are hard gates, not preferences.

### 5.1 No leakage

For outer LOSO fold `s`:

- no count from station `s`, on any date, may construct that fold's targets,
  priors, bounds, assignment scale, pool quotas, treatment selection, or
  hyperparameters unless the experiment is explicitly labelled “new sensor
  added” rather than LOSO recovery;
- any corridor or movement prior derived from station `s` must be excluded;
- treatment configuration must be frozen before reading fold `s` outcomes;
- aggregate inspection of all six held outcomes may select a future treatment,
  but its claimed performance requires a new untouched temporal validation set.

### 5.2 Do not weaken existing gates

Do not lower or bypass:

- sensor snap/semantics validation;
- PFE measurement bands or relaxation reporting;
- route/candidate/agent provenance;
- route legality and 75% routed-supply floor;
- calibrated structural guards;
- TAG thresholds or hourly aggregation;
- health, exactness, or annual-warming content-key checks.

### 5.3 Separate hypotheses from decisions

Every evidence artifact must label fields as one of:

- `measurement`
- `derived_exact`
- `diagnostic`
- `model_assumption`
- `decision`

A topology-based route-support quota is a model assumption. A counted turning
movement is a measurement. They must never share an unlabeled field.

### 5.4 Reproducibility

Every expensive run must record:

- Git commit and dirty-diff hash;
- exact source and input SHA-256 values;
- network, candidate-pool, direction-split, and demand-build hashes;
- treatment name and complete configuration;
- random seeds and their semantic use;
- wall-clock duration and tool versions;
- output paths and completion/failure status.

Never write evidence into a non-empty directory. Interrupted roots remain
failed evidence; start a new versioned root.

---

## 6. Work package 0 — freeze the baseline

### Objective

Make the corrected six-draw result independently verifiable before any
behavioral code changes.

### Tasks

1. Confirm the current focused tests:

   ```bash
   MPLCONFIGDIR=/tmp/gs-mpl PYTHONDONTWRITEBYTECODE=1 \
     python3 -m pytest -q tests/test_validate_sim.py tests/test_validate_dmrb.py
   ```

2. Confirm live TAG output:

   ```bash
   python3 tools/validate_dmrb.py
   ```

3. Verify every `inputs_sha256` entry in the complete sweep manifest against
   the current file bytes.
4. Freeze a tracked summary under `validation/` containing the values in
   section 3, manifest hash, input hashes, and live seed-42 report hash. Do not
   copy gigabytes of run artifacts into Git.
5. Record that the ignored run root is diagnostic evidence, not a release
   artifact.
6. Add a small test that rejects a baseline summary whose source manifest is
   incomplete or whose per-seed protocols differ.

### Outputs

- `validation/corrected_loso_sweep_v2_summary.json`
- focused regression test for the summary contract

### Acceptance gate

- six unique seeds;
- six distinct candidate hashes;
- protocol exactly `loso_pfe_meso_v5` in every run;
- all held component edges received assignment ceilings;
- TAG input hashes match;
- source manifest status is `complete`.

No later work starts if this gate fails.

---

## 7. Work package 1 — diagnostic instrumentation without behavior changes

### Objective

Explain why each held station receives its predicted flow. This package must
not change candidate generation, route weights, PFE solutions, or SUMO output.

### 7.1 Candidate-pool support audit

Add `tools/audit_loso_route_support.py` or an equivalent module under
`traffic_sim/confidence/`. For every seed and station, report:

- number of raw candidate legs crossing the held location;
- number of deduplicated `route × purpose` variables crossing it;
- number of unique edge sequences;
- OD-class, gate-pair, purpose, and tour-leg composition;
- number of unique OD groups and median alternatives per OD group;
- effective alternatives using normalized path-size seed weights;
- cost stretch versus the fastest route for the same OD;
- edge-overlap/path-similarity distribution;
- routes that cross both the held station and each remaining measured station;
- routes that cross the held station but no active fold constraint;
- orphaned-tour-leg counts by direction and purpose;
- pairwise Jaccard stability of station-crossing route sets across seeds.

For station 107, aggregate both component edges as one location while retaining
directional diagnostic rows.

### 7.2 Junction movement audit

Build the legal directed movement table for node `26355153`. For each incoming
edge → outgoing edge transition, record:

- whether SUMO permits the connection;
- how many candidate routes use it;
- how many deduplicated PFE variables use it;
- selected PFE flow by quarter;
- simulated SUMO flow by quarter;
- which measured edge constraints share those routes;
- support variation across seeds.

This is a route-transition diagnostic. Do not convert candidate frequency into
a claimed observed turn ratio.

### 7.3 Picker decomposition

**Completed 2026-08-09.** The implementation is validation-only in
`traffic_sim/confidence/picker_diagnostics.py` and runs after the ordinary
continuous solve. It deliberately does not edit `pfe.py`, so production bytes
and the warming source seal remain unchanged. It records final continuous
solutions rather than the originally proposed 200-iteration residual history;
iteration tracing remains deferred because it is not needed to explain this
failure. The paired stage decomposition is in
`validation/loso_picker_decomposition_v1.json`.

Add an opt-in diagnostics sink to `traffic_sim/demand/pfe.py`. Preserve the
numeric operation order and output bytes when diagnostics are disabled.
Record, per quarter and fold:

- target, bound, prior, structure-group, and purpose constraints active;
- count of routes touched by each constraint;
- routes with positive final flow;
- flow contribution by OD group/purpose/movement;
- assignment ceiling value and achieved flow for each held component edge;
- whether the ceiling is within a small tolerance of binding;
- relaxation rung used;
- level-1 residual trace and a sampled post-burn-in stability trace;
- final path-size seed weight and selected flow.

Do not alter `IPF_MAX_ITERATIONS`, burn-in, averaging, or the ceiling
multiplier in this package.

### 7.4 PFE-to-SUMO time displacement

**Daily-total mechanism closed 2026-08-09.** Exact published route-edge
entries and simulated entries differ by 0–6 vehicles in the six paired folds,
while integer publication changes held entries by hundreds to thousands.
Vehicle-level timing attribution is therefore unnecessary for the current
daily-ratio fault. Reopen it only for a quarter-level timing question.

For one frozen diagnostic seed, retain enough vehicle-route output to map:

- chosen route and planned departure quarter;
- actual entry time at each held sensor edge;
- planned count quarter versus actual count quarter;
- delayed insertion, rerouting, truncation, or missing entry.

Classify each residual as:

1. route selected but entry shifted to another quarter;
2. route selected but never entered the edge;
3. candidate/PFE support absent;
4. integer repair/bound effect;
5. unexplained.

Use SUMO's documented exit-time validation semantics as the reference. This is
diagnostic replay, not release evidence.

### 7.5 Files likely involved

- `traffic_sim/confidence/loso.py`
- `traffic_sim/demand/pfe.py`
- `traffic_sim/demand/pfe_kernel.py` only if trace extraction cannot remain at
  the Python boundary
- `build_candidates.py` metadata readers, not generation logic
- new diagnostic tool(s)
- `tests/test_validate_sim.py`
- `tests/test_pfe.py`
- `tests/test_pfe_kernel.py` if the kernel interface changes

### 7.6 Acceptance gate

- treatment disabled produces byte-identical calibrated routes on a fixed
  fixture;
- all diagnostics carry source hashes and measurement/assumption labels;
- node `26355153` reports eight legs and the expected three measured edges;
- station 107 remains one evaluation location;
- a synthetic fixture proves binding and non-binding ceilings are identified;
- a synthetic delayed vehicle is attributed to the correct adjacent quarter;
- no production-bound result changes.

---

## 8. Work package 2 — observability and data strategy

### Objective

Determine what additional information would identify the shared junction and
which information can be obtained without contaminating validation.

### 8.1 Rank missing information before requesting data

Using the movement audit, calculate an information-gain proxy for each possible
additional observation:

- each currently unmeasured junction leg;
- each legal turning movement;
- paired point-to-point/plate or trajectory observation through the junction;
- another link counter upstream or downstream.

At minimum, compare how adding each hypothetical observation changes:

- rank of the route-to-count incidence matrix;
- null-space dimension;
- condition number after reasonable scaling;
- predicted variance of held-edge flow under the existing route ensemble;
- number of previously indistinguishable movement patterns.

Use this to produce a ranked data request. Do not simply choose the edge with
the worst current ratio.

### 8.2 Preferred field data

The highest-value likely request is 15-minute turning-movement counts for all
legal movements at node `26355153`, ideally:

- at least two ordinary weekdays;
- the same time coverage as the modelled day;
- vehicle totals first; classes only if reliably counted;
- clear direction, approach, exit, timestamp, missing-data, and quality fields;
- no aggregation that prevents reconstruction of link totals;
- independent collection dates reserved for final validation.

TAG recognizes that single-day manual classified counts are common and may be
less stable than automatic link counts. Preserve collection-date and quality
metadata and report uncertainty.

### 8.3 If no new field data are available

Allowed:

- topology-only movement support;
- physically justified capacity intervals;
- exact conservation only where all but one leg are known;
- broad sensitivity scenarios for alternative turn ratios, labelled as model
  assumptions;
- information-gain recommendations for future sensors.

Not allowed:

- inferring a “prior” from station 134 or 2276 and then claiming those same
  stations were held out;
- normalizing candidate frequencies and calling them observed turn shares;
- choosing turn ratios that minimize outer LOSO error;
- forcing junction conservation when parking, driveways, missing legs, or
  temporal storage invalidate exact equality.

### Outputs

- `validation/junction_26355153_observability_v1.json`
- `docs/plans/JUNCTION_26355153_DATA_REQUEST.md`
- tests for incidence rank/null-space calculations on synthetic networks

---

## 9. Work package 3 — candidate-pool treatments

### Objective

Improve stable, plausible route support before asking the picker to choose a
better solution. Each treatment must be switchable so baseline and treatment
run through the same code revision.

### Treatment P1 — stratified support generation

Replace only the stochastic allocation of candidate-generation effort with
deterministic or seeded quotas across existing, independently grounded strata:

- OD class: E–E, E–I, I–E, I–I;
- purpose;
- gate pair or home/activity zone pair;
- departure period/day type;
- legal junction-movement support where relevant.

The quota definition must use population/activity/gate/topology inputs, never
held counts. Preserve overall generation budget initially. Measure whether
sensor support ratios and across-seed variance improve.

Do not impose equal candidates per sensor: that would use the evaluation
locations to shape the treatment and may overrepresent implausible trips.
Balance independent strata and observe the sensor consequence.

### Treatment P2 — reasonable multipath alternatives

For OD strata with one unique route, generate additional reasonable routes
using the existing travel-time cost model and perturbed weights. Pilot
alternative targets of 2, 4, and 8 unique paths only as support experiments.

Every alternative must pass the existing:

- route legality checks;
- U-turn and loop checks;
- local roundabout and whole-route stretch checks;
- provenance checks;
- candidate-supply floor.

Record cost stretch, overlap, and path-size weight. Do not count nearly
identical paths as independent success merely because their edge sequence
differs once.

### Treatment P3 — replacement for filtered tour partners

After a paired tour loses one leg to a route filter, attempt a bounded,
deterministic resample of only the missing leg while preserving:

- tour origin/home or activity endpoint;
- compatible gate or internal endpoint class;
- purpose and day type;
- direction and departure-period role;
- route-filter requirements;
- explicit lineage to the rejected partner.

Set a fixed attempt cap. If replacement fails, keep the existing standalone
support behavior and `tour_partner_dropped` label. Never lower the 75% supply
floor to make replacement appear successful.

### Treatment P4 — seed-stability contract

Separate random streams for:

- endpoint sampling;
- purpose sampling;
- departure sampling;
- route-cost perturbation;
- replacement tours.

Derive each stream from stable semantic keys so adding one route in a stratum
does not shift unrelated draws. This improves paired experiment validity and
makes seed sensitivity attributable.

### Treatment screening order

1. P1 alone
2. P2 alone
3. P3 alone
4. P4 as infrastructure if current streams are coupled
5. only then P1+P2 or P1+P3 combinations supported by diagnostics

Do not start with a full factorial. Each full six-seed arm costs roughly the
same as the completed 2 h 18 min sweep, excluding development and cache misses.

### Files likely involved

- `build_candidates.py`
- `build_sumo_demand.py` for option/metadata plumbing
- `traffic_sim/demand/provenance.py`
- `demand/intake.py` if day-block seed derivation changes
- `tests/test_build_candidates.py`
- `tests/test_build_sumo_demand.py`
- `tests/test_demand_provenance.py`

### Pool-treatment acceptance gate

Before LOSO, a treatment must:

- pass all route legality and provenance tests;
- retain or improve the 75% supply margin without changing the floor;
- preserve measured-edge feasibility;
- reduce the share of OD strata with only one effective path;
- improve station support balance as an observed consequence, not a hard
  target;
- not worsen path overlap or cost stretch beyond a pre-registered limit;
- preserve calibrated structure, purpose, and trip-length guards.

Treatments failing this static gate do not consume a six-draw LOSO campaign.

---

## 10. Work package 4 — picker and constraint treatments

### Objective

Change the picker only where diagnostics show that adequate plausible support
exists but the estimator systematically chooses the wrong movement pattern.

### Treatment K1 — measured turning-movement constraints

If independent turn counts are acquired, add transition constraints to PFE:

- a movement is `(incoming_edge, outgoing_edge)`;
- precompute its route-touch index alongside edge-touch indices;
- support exact targets or measurement-error intervals;
- retain per-quarter missingness;
- add provenance identifying source, date, and whether the count is calibration
  or validation data;
- exclude a held movement/location from any fold in which it is evaluated.

This is the most defensible picker improvement because SUMO and TAG both treat
turning movements as first-class observations.

### Treatment K2 — assumption-only movement sensitivity

If no turn counts exist, do not silently add K1. Instead define a small number
of explicitly labelled turn-ratio scenarios and report closure-result
sensitivity across them. These scenarios may bound uncertainty but cannot be
used to claim improved observed validation.

### Treatment K3 — assignment-field uncertainty, not blind tightening

First measure per-edge ceiling utilization and assignment residuals. Only if
diagnostics show a useful rank relationship should the assignment field be
changed.

Do not tune the `5×` ceiling multiplier on outer held counts. A candidate
replacement must provide an uncertainty model—for example, quantile bounds
from independently generated assignment draws—and must rebuild the field with
the held sensor absent. Compare the old wide ceiling and new uncertainty bound
as paired treatments.

**Screened and rejected as the current quality treatment (2026-08-09).** V7
enforced the existing held-edge ceiling at integer publication on two
contrasting seeds. All gates passed, but 134 stayed at 2.49–2.53, 107 stayed
widely seed-sensitive, and 2276 improved only 0.04–0.06 in daily ratio. This
does not justify changing the uncertainty model or running six seeds. The
integer-enforcement contract remains; quality work moves to discrete route
selection under non-held criteria.

### Treatment K4 — convergence/stability rule

Instrument the existing 200-iteration trace first. A future stopping rule must:

- preserve hard feasibility;
- preserve a fixed post-burn-in sample count or demonstrate an equivalent
  estimator;
- use a stability criterion, not only a one-iteration residual;
- be bitwise or tolerance-equivalent on fixed fixtures;
- be evaluated primarily as a speed change, not credited with LOSO quality
  unless it genuinely changes the estimator by design.

### Treatment K5 — joint controlled integer rounding

**Targeted screen passed; broader campaign deferred (2026-08-09).** Replace
the fold-dependent sequential initial repair with a joint integer projection:

- enforce each active rounded measured margin jointly;
- preserve the rounded continuous interval total;
- minimize route-level L1 deviation from the continuous PFE solution;
- keep the held station absent from constraints, objective and tie-breaks;
- use floor/ceil route bounds first and a general exact L1 fallback only when
  required;
- fail or explicitly classify mutually inconsistent integer margins instead of
  leaving order-dependent residuals;
- pass the result through the same purpose, structure and held-bound publisher.

The targeted v8 screen improves both 134 and 2276 in both seeds by 0.557–0.995
in daily ratio with zero active-sensor residual. Post-publication attribution
shows that repair adds 1,353–1,598 held entries for 134 while removing 30–214
for 2276. This selects K5 for the next diagnostic stage, not for production.
Before a six-seed run:

1. instrument 134 by repair class and test including continuously active
   purpose/structure/bound constraints in one joint projection;
2. address 2276 with non-held support or regularisation—the downstream repair
   is now ruled out as its remaining cause;
3. cache the fold incidence matrix to keep the treatment cost bounded;
4. for station 107, repair independently justified movement support at node
   `26355153` before another picker treatment. Its rung-band controlled
   rounding is technically valid but quality-rejected (`1.333/2.001`).

Do not edit production `pfe.py` until the all-station treatment survives those
checks and the warming Decision B cost is explicitly accepted.

### Explicit non-treatments

- Do not remove path-size weighting; it already addresses overlapping routes.
- Do not re-add a parsimony mechanism by folklore; measure active variables
  first.
- Do not tighten thousands of mostly inert ceilings globally.
- Do not change TAG, GEH, hourly grouping, or held-station semantics.
- Do not use the held sensor to select a PFE regularization coefficient.

---

## 11. Work package 5 — paired experiment harness

### Objective

Compare baseline and treatment with common seeds, identical validation logic,
and evidence that cannot be cherry-picked.

### 11.1 Harness changes

Generalize `tools/run_corrected_loso_sweep.py` to accept:

- `--label`
- `--variant-config <json>`
- exact `--seeds`
- `--baseline-or-treatment`
- an immutable output root
- optional diagnostic outputs
- a final-live-publication flag separate from evidence generation

Bind the runner itself and the complete variant config into the manifest.
Snapshot or hash every relevant implementation input.

Prefer one code revision with feature switches over comparing two unrelated
commits. That keeps shared code, validation, and instrumentation identical.

### 11.2 Seed contract

Use the established ordered seeds:

`20260807, 20260808, 20260809, 20260810, 20260811, 42`

For every treatment seed, use the same semantic random streams as the baseline.
If treatment logic necessarily adds events, stable keyed streams from P4 must
prevent unrelated random draws from shifting.

### 11.3 Run sequence

1. Run unit and fixture tests.
2. Run one seed as a **diagnostic pilot** only to catch implementation defects.
   Do not use it to choose among quality treatments.
3. If static gates pass, run all six paired seeds.
4. Apply TAG separately to each seed.
5. Produce paired deltas by seed and station.
6. Do not publish a live report until the arm is complete and selected.

### 11.4 Primary outcomes

- overall TAG satisfactory share per seed;
- station-level TAG share;
- daily simulated/measured ratio;
- absolute log-ratio `abs(log(simulated/measured))`;
- hourly bias and absolute error by time period;
- seed-to-seed median absolute deviation and range;
- route-support and movement-support metrics;
- measured-edge calibration and relaxation rungs;
- structural, purpose, provenance, and health gates;
- runtime and memory, reported separately from quality.

### 11.5 Pre-registered promotion gate

A treatment advances from experiment to temporal validation only if all hard
gates pass and, across the six paired seeds:

1. median overall TAG share improves by at least **5 percentage points**;
2. at least **4 of 6** paired TAG deltas are positive;
3. no seed loses more than **5 percentage points** overall;
4. median absolute log-ratio across stations improves by at least **20%**;
5. each of 134 and 2276 improves its median absolute log-ratio by at least
   **25%**;
6. no previously stable station worsens its median absolute log-ratio by more
   than **10%**;
7. seed variability does not materially increase;
8. measured fit, relaxation, structure, route legality, provenance, and health
   do not regress.

These thresholds are project decision rules, not claims that TAG defines a
5-point improvement as sufficient. TAG compliance remains the unchanged
external target. If an actor wants to change these promotion rules, it must do
so before seeing treatment outcomes and record the reason.

### 11.6 Statistical reporting

Report:

- all six paired values, not only averages;
- median and interquartile range;
- paired deltas and their sign count;
- a bootstrap interval only as exploratory context, clearly noting that six
  pairs are too few for a strong asymptotic claim;
- station-level outliers;
- no unpaired t-test and no “best seed” result.

---

## 12. Work package 6 — untouched temporal validation

### Objective

Prevent treatment selection on the same 2025-09-16 observations from becoming
the final claim.

### Procedure

1. Before inspecting alternative dates, freeze the selected treatment and its
   configuration.
2. Choose one or more 2025 dates with:
   - the same behavioral day class;
   - at least 90% independent sensor coverage;
   - no use during treatment development;
   - documented unusual-event/holiday status.
3. Use `validate_sim.py --holdout-date YYYY-MM-DD` with the corrected temporal
   protocol.
4. Run matched seeds for baseline and selected treatment.
5. Apply the same station semantics and hourly TAG evaluator.
6. Report same-window LOSO and temporal transfer separately.

### Acceptance gate

- no treatment configuration changed after temporal results were read;
- source/pool/network hashes are frozen;
- coverage guard passes;
- paired improvement is directionally consistent with the development date;
- no claim of formal TAG certification from six stations alone;
- intended-use limitations are documented.

If the treatment fails temporal transfer, do not promote it even if
2025-09-16 LOSO improves.

---

## 13. Warming decision tree

### Current state

At plan creation:

- succeeded: `74,094 / 104,685` (`70.8%`)
- failed: `0`
- pending: `30,588`
- database rows marked running: `3`, believed stale after the stopped process
- plan content key:
  `adf91205bfcafc0cebbb18613e064e49fa9d3321758638c418e36d41552b30b2`
- current plan verification: pass

Never copy the key into an execution command without re-reading and verifying
the plan. The literal above is historical context, not authorization.

### Decision A — diagnostics only

If changes touch only validation, diagnostics, tests, or documentation and
`tools/plan_annual_warming.py --verify` still passes:

- existing successful states remain compatible;
- no reset or re-warm is required;
- stale running rows may be retried by the normal resume path.

### Decision B — no production treatment accepted

Resume the current bank with the same verified plan. Completed states are
durable and skipped. Do not relabel an older root.

### Decision C — production treatment accepted

Changes to candidate generation, PFE behavior, assignment priors, demand
assembly, route publication, or another bound source require:

1. stop/pause any population process;
2. regenerate the annual plan;
3. verify the new content key;
4. regenerate/verify preflight;
5. initialize a new root for the new key;
6. preserve the old 74,094-state bank as superseded evidence;
7. never copy, relabel, or mix artifacts between keys;
8. populate only after all release-relevant tests pass.

### Recommended timing

Keep warming paused through work packages 0–5 because a successful pool or
picker treatment would invalidate the remaining work and eventually require a
new bank. At the first production decision gate:

- rejected/no production change → resume current bank;
- accepted production change → create the new plan and bank.

This is a cost decision, not a validation gate. The already banked states are
not corrupted by the diagnostic work.

---

## 14. Test matrix

### Always-run focused tests

```bash
MPLCONFIGDIR=/tmp/gs-mpl PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q \
  tests/test_validate_sim.py \
  tests/test_validate_dmrb.py \
  tests/test_assignment_priors.py \
  tests/test_observability.py \
  tests/test_pfe.py \
  tests/test_pfe_kernel.py \
  tests/test_build_candidates.py
```

Add `tests/test_build_sumo_demand.py` and provenance tests whenever option
plumbing or candidate publication changes.

### Required new unit coverage

- physical-station aggregation for total and directional sensors;
- movement-touch indexing;
- held movement/prior exclusion;
- assignment ceiling value and binding classification;
- route-support metrics and effective-alternative calculation;
- stable keyed random streams;
- orphan replacement success/failure/provenance;
- paired manifest seed/config equality;
- refusal of non-empty evidence roots;
- temporal-treatment freeze guard;
- warming-plan seal drift when a production-bound file changes.

### Required integration coverage

- a synthetic junction with known turns recovers a held link;
- a junction with multiple unknown legs remains underdetermined;
- diagnostic mode is byte-identical to normal mode;
- baseline and treatment use identical seeds and validation protocol;
- a deliberately leaked held prior is rejected;
- a delayed SUMO entry is attributed to timing displacement;
- final seed-42 publication equals its evidence copy when live publication is
  explicitly requested.

---

## 15. Evidence artifacts and schemas

Use versioned JSON with `allow_nan=False` and atomic temporary-file replace.

### `corrected_loso_sweep_v2_summary.json`

Required fields:

- schema/version/status;
- source manifest path/hash;
- Git/diff identity;
- protocols;
- seeds and candidate hashes;
- per-seed TAG results;
- station aggregates;
- assignment-ceiling completeness;
- interpretation and limitations.

### `loso_route_support_audit_v1.json`

Required fields:

- inputs/provenance;
- station semantics/component edges;
- route/OD/purpose/movement support metrics;
- per-seed stability metrics;
- no quality verdict unless an explicit rule is included.

### `junction_26355153_observability_v1.json`

Required fields:

- node and legal leg/movement inventory;
- measured, derived, and unknown classification;
- incidence rank/nullity;
- hypothetical-observation information gains;
- data request ranking;
- assumptions separated from measurements.

### `loso_treatment_comparison_v1.json`

Required fields:

- baseline and treatment identities;
- exact paired seeds;
- hard-gate results;
- paired metrics and deltas;
- promotion-rule evaluation;
- temporal status;
- warming-seal impact;
- decision: reject / continue / promote.

---

## 16. Stop conditions

Stop and report rather than improvise if:

- the held observation appears in any fold input;
- baseline and treatment cannot share equivalent seed semantics;
- candidate provenance cannot map every selected route to its pool;
- the observed junction topology differs from the recorded eight-leg node;
- a proposed data source lacks direction or measurement semantics;
- an intervention improves TAG only by widening measurement bands or dropping
  existing gates;
- a production change is made while annual warming is actively writing;
- plan verification changes unexpectedly after validation-only work;
- three distinct treatment approaches fail for the same unresolved reason and
  no new evidence can distinguish them.

Do not stop merely because TAG remains below 85%. A truthful, quantified
fitness-for-purpose limitation is an acceptable result.

---

## 17. Definition of done

This improvement program is complete only when all of the following are true:

1. The corrected LOSO/TAG baseline is frozen and reproducible.
2. The pool and picker diagnostics explain the dominant station flows.
3. The 133/134/2276 junction's underdetermination is quantified.
4. Any new sensor/data request is ranked by information gain.
5. Candidate and picker changes were tested separately before combination.
6. A promoted treatment passed all hard gates and paired six-seed criteria.
7. The promoted treatment transferred to an untouched date.
8. Remaining outliers and intended-use limitations are documented.
9. The warming bank decision follows section 13 with no cross-key mixing.
10. `TASKS.md`, `AGENT_NOTES.md`, architecture/contracts, tests, and evidence
    records truthfully reflect the result.

---

## 18. Suggested first implementation task

The next actor should implement **work package 0 and the read-only parts of
work package 1**, not a behavioral fix.

Concrete first slice:

1. Freeze `validation/corrected_loso_sweep_v2_summary.json`.
2. Implement `tools/audit_loso_route_support.py` for:
   - station component aggregation;
   - unique route/OD counts;
   - effective alternatives;
   - node `26355153` legal movement support;
   - across-seed support stability.
3. Add unit tests with a tiny synthetic candidate set.
4. Run it against all six existing evidence roots.
5. Write a short evidence-backed decision selecting P1, P2, P3, or a data
   request as the next treatment.

This slice is read-only with respect to demand behavior, does not invalidate
warming, and supplies the evidence needed to avoid another blind tuning pass.

---

## 19. Handoff template for the next actor

Use this exact structure in `AGENT_NOTES.md` when handing off:

```text
- Focus and status:
- Objective completed or attempted:
- Files changed:
- Exact tests and results:
- Evidence artifacts and hashes:
- Baseline/treatment seed contract:
- Leakage checks:
- Pool findings:
- Picker findings:
- Junction-observability findings:
- TAG and station-level paired deltas:
- Hard gates passed/failed:
- Warming plan verify result and population status:
- Decision and reason:
- Remaining blocker or next smallest useful action:
```

The handoff must distinguish measurement, hypothesis, result, and decision.
Do not report a single-seed improvement as a model improvement.

---

## 20. Execution update — robust production pool/picker v13

The generic production slice of this plan is implemented and recorded in
`validation/pool_picker_robustness_v13_20260809.json`.

- Registry/flow/cache identity, canonical sensor order and canonical day-type
  geometry are fail-closed for future sensor additions.
- Final candidate support is measured with distinct physical routes; candidates
  and published vehicles must cross a current registry edge.
- One bounded deterministic reroute recovers grounded requests rejected only by
  randomised routing, without changing OD/via intent or weakening any filter.
- Purpose duplicates no longer inflate path-size overlap.
- Two real pool seeds pass the physical contracts with 9,280/9,309 candidates,
  5,964/5,998 unique routes, minimum sensor support 516/527 and zero unanchored
  candidates.
- The focused implementation surface has 502 passing tests. The broad suite was
  stopped at 72% after 2,841 passes, 102 known stale historical freeze-contract
  failures and a long integration wait; those contracts are outside this pool/
  picker slice and were not weakened or rewritten.

This is a production robustness promotion, not completion of the scientific
definition of done above. Station 107 became slightly worse (ratio 1.615→1.644),
station 134 improved hourly fit (GEH<5 87.5%→91.7%) with mixed daily ratio, and
all six station certificates remain underidentified. Do not spend more LOSO
runs merely to tune these two stations and do not claim TAG acceptance. The new
annual warming plan/preflight are valid but population remains unstarted.

## 21. Execution update — structure closure, temporal holdout and bounded pilot

The integer structure loop now retains every previously activated optional
group and uses one shared discrete cap in both repair and audit. The active
local build is `dbb44172f30778adf8c0`: all short-trip cap violations are gone,
all vehicles remain sensor-anchored, baseline PFE and raw hourly SUMO sensor
fit are both GEH<5 `100%`, and the standard closure is bound to the same build.

The fresh 2025-09-17 temporal run still fails the TAG-aligned diagnostic
(`65.0%` GEH<5; `82.5%` satisfying either flow difference or GEH, versus an
`85%` guideline). All six held stations have rank gain 1. The decision is
therefore unchanged: improve absolute LOSO only after independent movement
information reduces the rank gap; do not tune the pool against held values.

The new annual plan `9b640a0c…` and preflight `916e0f84…` pass. Two bounded q10
state units succeeded and verified cache/resume plus predecessor extension;
104,683 units remain and the full population has not been started. One small
q50 purpose-length median inversion remains a disclosed behavioural warning,
not a reason to introduce an unmeasured picker constraint.
