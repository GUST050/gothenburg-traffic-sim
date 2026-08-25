# Canonical Route Catalog Plan

**Date:** 2026-08-24  
**Status:** Implemented, qualified, adopted and operationally verified on
2026-08-24. Production defaults to the verified catalog;
`--candidate-source legacy` remains the explicit rollback. One bounded
warm-state pilot and the refreshed annual preflight passed, but annual warming
remains intentionally disabled and was not launched.  
**Purpose:** Stop rebuilding and rerouting substantially the same candidate
geometry for every calendar day. Reuse a bounded weekday/weekend route catalog,
let PFE perform the date-specific selection, and continue warming only the exact
finished daily demand.

## Decision

Build two immutable, content-addressed routed catalogs:

- `weekday`
- `weekend` (also used for holidays under the existing classification rule)

The catalog owns plausible route supply. The daily PFE picker owns the date's
15-minute sensor targets, purpose margins, integer vehicle counts, provenance
selection and departure times. The calibrated day library continues to own
finished date-specific route and agent artifacts. The warm-state cache remains
bound to the exact finished route file, variant and seed.

This is deliberately different from `tools/standard_driver_pool.py`. That tool
is an isolated post-picker driver/departure experiment and remains inactive.
The catalog in this plan lives before PFE and does not prescribe a finished
standard day.

## Why this is the next useful boundary

The current active one-day build records:

| Stage | Wall time |
|---|---:|
| candidate generation plus `duarouter` | 37.677 s |
| PFE variants, integer projection and writing | 37.612 s |

The candidate cache currently contains 95 entries and occupies about 2.8 GiB,
while the calibrated day library contains 21 unique dates. Historical entries
contain about 6,000 unique routes in the common one-day size band, but their
manifests do not retain enough config to isolate date from seed, pool key,
source revision and size. They therefore prove storage/recomputation volume,
not the load-bearing geometry-invariance claim. That claim must be established
by Stage 0 on two fresh, fully bound builds. A catalog must in every case be
built from the canonical template before daily resampling. It must not be
copied from one arbitrary day's route XML or formed by blindly unioning
historical days.

The code already supplies the main invariants:

- `demand.intake.pool_key_for` defines the weekday/weekend boundary.
- `build_candidates.CandidateStructure` contains expensive date-invariant
  inputs.
- `day_type_template_seed` separates canonical geometry randomness from the
  exact-date departure stream.
- `pfe.prepare_calibration` works on distinct route-geometry × purpose shapes.
- `pfe.calibrate` already makes every shape available to every quarter and
  assigns departures after selection.
- `DayLibrary` already verifies and atomically stores finished daily artifacts.
- `WarmStateIdentity` already fingerprints the exact route file and must not be
  weakened.

## Simple target architecture

```text
network + sensor registry + structural demand model + router runtime
                              |
                              v
             canonical weekday/weekend route catalogs
                              |
       daily targets + day type + daily purpose/time margins
                              |
                              v
                 existing PFE and integer projection
                              |
                              v
             verified date-specific DayLibrary artifact
                              |
                              v
               exact per-day/per-seed warm state
                              |
                              v
                     closure simulation
```

No closure request builds demand. No generic catalog is loaded as a SUMO warm
state. The catalog accelerates cold daily builds and warming preparation; it
does not by itself reduce the runtime of a closure whose exact daily warm state
already exists.

## Scope and non-goals

### In scope

- Persist canonical routed weekday/weekend templates once per structural
  identity.
- Remove calendar date and real-day traffic values from route-catalog identity.
- Pass the date's purpose × quarter margins explicitly to PFE.
- Keep exact whole-vehicle sensor publication and all existing structure,
  provenance and health gates.
- Reuse the existing day library after PFE and the existing warm-state cache
  after daily publication.
- Support the declared 50-physical-station target without per-date rerouting.

### Not in scope

- A static annual vehicle file.
- A pre-warmed generic population.
- A database, service, Kubernetes component or generic Python worker pool.
- Changing SUMO's traffic model, closure rerouting or seed semantics.
- Adding/removing vehicles to make raw SUMO passage time match sensors.
- Catalog reuse for `--congestion-iterations > 1`. That existing mode derives
  feedback weights from the date's calibrated traffic and deliberately
  regenerates routes. V1 applies only to the default free-flow
  `--congestion-iterations 1` path; higher values explicitly bypass the catalog
  and retain today's feedback loop. A congestion-specific catalog is not a
  silent fallback and would require a separate future plan.
- Deleting the current candidate cache or existing warm evidence during
  implementation.

## Artifact contract

### Catalog identity

Create `CatalogIdentity(schema_version=1, pool_key, config, inputs,
source_files, runtime)` in `traffic_sim/demand/route_catalog.py`.

Do not create a second hand-maintained fingerprint inventory. First extract the
current candidate key construction into one shared
`candidate_identity_components()` function. Derive the catalog identity
subtractively from that complete structure:

```text
catalog components := current candidate components
                      minus explicitly classified date-only components
```

The initial date-only set is limited to `start_date`, the values of
`source_flows`, `real_day_shape` and `day_blocks`. Because
`build_candidates.py` still reads `web/data/flows.json` to validate the sensor
edge set, replace the removed flow file with a canonical `source_flow_edge_set`
projection and prove it equals the reviewed registry. Removing daily values
must never remove the sensor-set contract. Any new candidate identity field
which is neither retained nor explicitly classified date-only must fail the
catalog identity test, not silently disappear.

The retained components currently include every input that can change route
availability, geometry, meaning or reproducibility, including:

- `pool_key`: `weekday` or `weekend`
- exact network bytes
- reviewed sensor registry and loader/source code
- graph, map geometry, population, DeSO, building and POI inputs
- `normal_profile.json`, `direction_split.json` and the canonical
  pool-departure support rule
- endpoint/gate assignment priors
- `n_total`, `min_per_sensor`, through/cross/gravity, route-diversity, stretch,
  local-stretch, atomic-tour and support parameters
- canonical template seed
- routing weight regime and weight-file content when used
- exact `duarouter` executable bytes and reported SUMO version
- Python/platform and relevant NumPy, NetworkX, OSMnx and Shapely versions
- all current candidate-builder/cache source files, including `build_data.py`,
  `dirsplit/geo.py`, `demand/locations.py` and
  `traffic_sim/intake/direction_anchor.py`, plus new catalog code

The key must not include:

- calendar date
- measured or forecast 15-minute values for one day
- the day's real departure profile
- output directory
- generated timestamps

This list documents the extracted identity but does not independently implement
it. The shared component builder plus the subtractive classification is the
authority. Daily target bytes belong to `DayIdentity`.

### Catalog contents

Keep v1 intentionally small and explicit:

1. `catalog.rou.xml`
   - named, connected route edge sequences represented by the existing
     candidate-vehicle interchange format;
   - a neutral, day-type-controlled departure clock which is adapter input,
     not the final day's published departure schedule;
   - deterministic IDs for identical catalog inputs.
2. `catalog.meta.json`
   - one or more source-template records per route;
   - purpose, tour kind/leg, OD/via and endpoint-pool provenance;
   - sensor signature and support-only status;
   - generation and filtering diagnostics.
3. `catalog.validation.json`
   - route count, unique geometries, OD pairs and covered edges;
   - distinct geometry support per registered sensor;
   - connectedness, U-turn, stretch and unanchored-route results;
   - complete identity and artifact digests.
4. `catalog.template.json`
   - the pre-resampling canonical-template count and semantic SHA-256 used by
     the two-date invariance gate.
5. `manifest.json`
   - schema/kind/key, identity, exact bytes and SHA-256 for every artifact;
   - written last after all validation passes.

Do not over-normalize v1. Preserve separate source-template records when they
carry different purpose or endpoint provenance even if their edge sequence is
the same. PFE already deduplicates the numerical variable correctly while
retaining those sources for later provenance allocation.

### Daily purpose-margin semantics

Stage 1 has two different obligations which must not be conflated:

1. **Refactor proof on the legacy path.** Explicitly passing the margins
   inferred from the same surviving legacy candidates must reproduce current
   results exactly. This proves the new API wiring only.
2. **Catalog behavior contract.** The legacy inferred margin is partly a
   product of date-specific route filtering: U-turn, invalid-route and detour
   removal reject purposes/legs at different rates. It cannot be the permanent
   definition after those date-specific candidate files disappear.

Freeze `daily_purpose_margin_v1` as follows for the catalog path:

- start from the verified catalog's surviving source-template records;
- apply the date's normalized day profile and existing purpose-by-hour model
  to those records;
- apply the existing explicit activity-category shares and through-share
  target;
- normalize the resulting purpose weights per quarter before integer
  allocation;
- fail catalog validation when a required purpose/category has no adequate
  compatible route support instead of silently changing the margin.

Thus route filtering defines a fixed, reviewed support set once; it no longer
changes a day's behavioral margin accidentally. Stage 4 compares this declared
catalog semantic against all structure, purpose, LOSO and population gates. The
Stage 1 legacy equality is not presented as proof that catalog output must be
byte-identical.

### Daily identity

`DayIdentity` remains date-specific. Replace its date-specific
`candidate_pool` and `candidate_metadata` hashes with:

- the ordered catalog key set used by that build;
- the catalog-composition rule (`weekday`, `weekend`, or both);
- exact daily targets, bounds, priors and explicit purpose margins;
- existing picker/runtime/source fingerprints.

Changing a date or its counts rebuilds only the daily artifact. Changing the
network, sensors, catalog model or router changes the catalog key and therefore
invalidates dependent days and warm states automatically.

### Warm-state identity

No schema change is planned. Warming continues after PFE and fingerprints the
exact daily `calibrated*.rou.xml`, demand build, variant, seed, network,
additional files, source inventory and SUMO runtime. Never key a warm state on
the generic catalog alone.

## Implementation stages

Each stage is independently testable. Do not start the next stage while a hard
gate in the current stage is red.

### Stage 0 — Freeze the comparison, no behavior change

1. Repair the existing `build_candidates.py --help` formatting failure and add
   a subprocess regression. Bare percent signs in argparse help strings must be
   escaped as `%%`; this is a CLI-only prerequisite, not a catalog behavior
   change.
2. Run the load-bearing two-date experiment before adding catalog storage:
   - choose two ordinary weekdays with different daily profiles;
   - bind identical network, sensor registry, structural inputs, parameters,
     seed, runtime and free-flow router;
   - capture and compare the canonical templates *before* daily purpose
     resampling and departure assignment;
   - route the same neutralized canonical template twice with stable IDs and a
     catalog-controlled departure clock, and compare semantic route/provenance
     digests;
   - separately record the current final date-specific candidate sets, which
     are not expected to be identical because daily resampling and randomized
     routing currently occur after the canonical-template boundary.
3. Gate the whole plan on that experiment:
   - if pre-resampling templates or neutral routed catalog digests differ, stop
     and identify the hidden input/randomness; Stages 2–5 are blocked;
   - do not respond by unioning daily pools or weakening deterministic gates.
4. Select a small isolated fixture set:
   - active weekday `2027-09-08`;
   - one weekend date;
   - one weekday holiday that follows weekend behavior;
   - one multi-day composition containing both catalog types;
   - the existing synthetic 50-station correctness fixture.
5. Record per build:
   - candidate wall time;
   - catalog/PFE preparation time;
   - solve, integer projection and publication time;
   - peak RSS and output bytes;
   - route/purpose/OD/structure summaries and every hard gate.
6. Use isolated output roots and empty caches for cold comparisons. Retain the
   current path as the baseline and rollback.

**Exit:** the CLI regression passes, canonical template plus neutral routing
are deterministic across the two dates, and reproducible baseline records
exist without altering live demand. Otherwise the plan stops at Stage 0.

### Stage 1 — Make daily margins explicit, preserve current results

1. Add one explicit `purpose_mixes_per_q` input at the PFE orchestration
   boundary. Reuse the existing internal format already accepted by the report
   writer.
2. On the legacy path, compute the explicit value from the same surviving
   candidates and current correction functions. Run inference and explicit
   margins together and require exact equality before switching that call.
3. Separately implement and report `daily_purpose_margin_v1` from the verified
   support plus daily behavioral model defined above. Do not claim that it is
   equal to the attrition-conditioned legacy margin.
4. Test catalog-margin support and structural consequences without publishing
   it as production behavior. The semantic choice is accepted only with the
   complete Stage 4 gates.
5. Keep fallback inference only for legacy/test callers that omit the new
   argument. A catalog call must always pass its explicit declared margins.

**Exit:** existing candidate bytes produce identical interval solutions,
integer counts, route/agent semantic digests and fit reports with explicit
margins, and the distinct catalog-margin contract is machine-readable and
fails closed on missing purpose support.

### Stage 2 — Build and verify canonical catalogs in isolation

1. Add `traffic_sim/demand/route_catalog.py` for identity, verification,
   restore and atomic publication.
2. Add a thin CLI `tools/build_route_catalog.py` that calls repository code;
   it must not duplicate generation logic.
3. Refactor `build_candidates.py` just enough to expose the canonical template
   before daily purpose resampling and departure assignment.
4. Route the canonical templates once per pool key with the same `duarouter`
   validity and recovery gates as today.
5. Select catalog size deterministically per sensor-registry identity:
   - start at today's applicable `n_total`;
   - if support fails, try a bounded 1.5× sizing ladder;
   - choose and record the first size that passes every distinct-geometry
     sensor-support and resource gate;
   - stop when the Stage 4 time/RSS/PFE-variable budget would be exceeded. If
     no size passes, fail closed and return to architecture review; never use a
     different size per calendar date.
6. Publish to `sumo/route_catalog/<key>/` through a sibling temporary
   directory. Validate all artifacts before renaming the directory and write
   `manifest.json` last.
7. Guard production with the existing bounded `content_key_lock`. After
   acquiring it, recheck whether a verified entry now exists so competing
   processes do not duplicate the expensive build.
8. A corrupt, missing or wrong-schema catalog is a cache miss. It must never be
   partially loaded and must never replace the last valid catalog.
9. Refuse catalog selection when `congestion_iterations > 1` and route through
   the existing feedback-specific candidate path with explicit provenance.

**Exit:** repeated builds with identical structural inputs restore the same
verified catalog; date-only changes do not change its key; every semantic input
change does.

### Stage 3 — Feed a catalog to the existing PFE

1. Add one adapter that exposes catalog routes and provenance in the format
   consumed by `prepare_calibration`. Prefer a small staged candidate XML/meta
   pair in v1 rather than rewriting the solver loader.
2. Give the adapter a catalog composition plus explicit daily margins. It must
   not generate new paths or call `duarouter`.
3. Keep the numerical PFE, relaxation ladder, joint integer projection,
   purpose-compatible provenance selection and atomic publication unchanged.
4. Add one isolated selector:
   `--candidate-source legacy|catalog`. Default stayed `legacy` through
   qualification and now resolves to `catalog` only through the verified
   adoption record. There is no third automatic fallback mode.
5. Record `candidate_source`, catalog keys, catalog restore/materialization
   timings and hit/miss status in demand metadata and provenance.

**Exit:** every fixture builds through the catalog path without date-specific
routing, while the legacy path remains available and unchanged.

### Stage 4 — Qualification and performance decision

Run paired, counterbalanced legacy-versus-catalog builds on the Stage 0
fixtures. At least 30 paired cold daily trials must cover both arm orders and
every selected day class; cache and output roots are separate per trial. Report
median, nearest-rank p95, maximum and paired spread. P95 is not allowed to be a
decision based on five samples per arm or on whichever arm received the single
cold maximum.

Hard correctness gates:

- exact rounded sensor targets for every directed edge × 15-minute interval;
- zero maximum and summed integer residual;
- no added or removed vehicles outside the picker's declared result;
- all final vehicles remain sensor-anchored where required;
- all connectedness, U-turn, stretch, endpoint and every-edge support gates
  pass;
- purpose-route compatibility and route/agent provenance pass;
- no new structure, confidence or LOSO hard failure;
- same deterministic result on repeated identical catalog builds;
- malformed catalog, killed producer and concurrent producer tests pass;
- day-library restore and exact warm-state identity tests pass in the recorded
  environment described below.

Performance gates:

- verified catalog restore plus PFE adapter p95 <= 5 s;
- median total cold-day wall time improves by at least 25% versus the paired
  legacy arm;
- no fixture day class has a slower paired median;
- PFE solve/integer/publication p95 regresses by no more than 5%;
- catalog path peak RSS stays within the existing 8 GiB product budget;
- the measured one-time catalog build, including any support-driven size
  increase, amortizes within at most three unique cold days using the measured
  per-day saving; do not extrapolate build time linearly from route count;
- no baseline or closure runtime regression after the same finished daily
  route bytes are supplied to SUMO.

If exact outputs differ because the catalog intentionally removes per-date
routing randomness, compare declared semantic and structural gates rather than
pretending byte equality. Any difference must be explained by the catalog
choice and recorded; no hard gate may be weakened.

**Exit:** publish one immutable qualification report with raw per-trial values,
catalog route/variable counts and selected sizing attempt, identities, gate
results and an explicit adopt/reject/inconclusive verdict.

### Stage 5 — Adopt with rollback, then prepare warming

Only after an `adopt` verdict:

1. Change the normal on-demand build default from `legacy` to `catalog`, but
   keep annual warming disabled.
2. Soak the catalog path across at least seven independent daily builds
   covering both day types and one mixed assembly. Exercise one rollback build
   and prove the earlier legacy day/warm identities remain readable.
3. Keep `--candidate-source legacy` as a tested rollback for at least one
   release cycle. Record that rollback after new warming invalidates all warm
   states produced from different catalog-derived daily route bytes; it is not
   operationally free.
4. Do not delete old candidate-cache entries. Add pruning support only as a
   separate, dry-run-first maintenance change after the new catalog is stable.
5. Rebuild one ordinary daily release through the catalog path, validate it,
   then atomically publish it through the existing release boundary.
6. Only after the soak and rollback drill, freeze the catalog/daily identity,
   run one bounded warm-state pilot and recompute annual warming preflight.
   The repository estimates annual warming at roughly 100–150 s and 32 MB per
   day-slot, about 30 h and 24 GB for a year, so warming before the identity is
   stable is explicitly forbidden. This plan does not launch an annual
   campaign automatically.
7. Update `ARCHITECTURE.md`, current status and operating documentation with
   measured results, not projected savings.

**Exit:** normal recalibration uses a verified canonical catalog, rollback is
tested, and exact daily warming remains correctly invalidated by daily output.

## Robustness and failure behavior

- **Fail closed on semantic invalidity:** missing sensor support, malformed
  provenance, disconnected routes or failed catalog validation aborts the new
  daily build. The active published release is untouched.
- **Fail open only as a cache lookup:** missing/corrupt cache data becomes a
  miss and may be rebuilt. It never becomes partially trusted input.
- **Atomicity:** build in an isolated sibling directory; fsync/rename where the
  existing storage convention requires it; manifest last.
- **Single-flight:** one producer per content key, bounded wait, visible wait
  message, timeout error and automatic flock release on process death.
- **Cancellation:** cancellation may stop catalog production but cannot remove
  an earlier verified entry or publish a partial new one.
- **Determinism:** stable ordering for sensor edges, route records, metadata and
  manifests; generated timestamps are not semantic identity.
- **Resource bounds:** one catalog producer at a time under the demand build
  lock. PFE keeps its existing bounded interval worker policy; no nested pool
  is introduced.
- **Provenance:** every daily build names the exact catalog keys and artifact
  digests. A catalog from another network/router/runtime is rejected before
  PFE.

## Required test structure

### Unit tests

- catalog identity is independent of date/daily count values;
- network, sensor, model, parameter, source and router changes invalidate it;
- weekday/weekend/holiday selection is correct;
- manifest schema, key, complete artifact set, hashes and sizes are verified;
- corrupt JSON/XML/gzip, missing artifact and wrong record type are misses;
- atomic publication leaves no trusted partial entry after injected failure;
- same-key threads/processes run one producer and timeout visibly when hung;
- explicit purpose margins match current inference on legacy candidates;
- catalog departures are not used to decide route availability or daily mix;
- deterministic route/provenance ordering and IDs;
- 50-sensor support checks are order-independent.

### Integration tests

- catalog build -> restore -> daily PFE -> DayLibrary store/restore;
- concurrent requests for one absent catalog;
- producer killed before and after artifact staging;
- exact single-day weekday, weekend and holiday builds;
- mixed weekday/weekend composition;
- exact q50 and opt-in stress variants without population drift;
- warm-state key changes when daily route bytes change, not when only the
  physical location of an identical catalog entry changes;
- legacy rollback still builds and publishes under the old contract.

### Evidence tests

- paired Stage 4 performance campaign;
- all existing demand/PFE/DayLibrary/warm-state focused suites;
- `make lint`, JavaScript syntax if web metadata changes, and
  `git diff --check`;
- a broader demand plus scenario regression before adoption.

Every recorded check must name its environment. The local focused command uses
`MPLCONFIGDIR=/tmp/gs-mpl` and `PYTHONDONTWRITEBYTECODE=1`. SUMO-backed tests
must additionally record the resolved `SUMO_HOME`, SUMO version and
`sumo/net.net.xml` digest. A missing SUMO binary/network, an environment skip or
a CI collection failure is not reported as “tests remain green”; pure unit and
SUMO integration results are reported separately with exact pass/skip/fail
counts.

Do not update frozen historical hashes merely to make them pass. A changed
scientific artifact needs a new evidence version; unrelated stale historical
freeze debt remains labelled as such.

## 50-sensor scaling rule

Adding or re-snapping sensors invalidates and rebuilds the two catalogs once.
It must not cause one catalog rebuild per date. The rebuilt catalogs must give
each resolved directed sensor edge the existing minimum number of distinct
physical route geometries and pass the incidence/observability checks.

The response to inadequate support is explicit: run Stage 2's bounded sizing
ladder and freeze the smallest passing `n_total` in catalog identity. Do not
lower `min_per_sensor`, repeat identical routes to satisfy it, or let the picker
invent support. If no catalog fits within the solver/RSS/time budgets, fail
closed and revise candidate generation before warming or production adoption.

More sensors may legitimately make PFE choose more daily vehicles. That changes
the finished day and its warm states, not the catalog architecture. Keep
separate timing for:

- catalog load/materialization;
- sparse incidence construction;
- continuous interval solving;
- integer projection and publication;
- SUMO warming and closure simulation.

This separation prevents a fast catalog lookup from hiding a slower 50-sensor
picker or a vehicle-capacity failure.

## Completion criteria

The task is complete only when:

1. Date-independent weekday/weekend routed catalogs exist with verified
   content identities and atomic storage.
2. The daily picker consumes explicit daily margins and no longer requires
   date-specific `duarouter` work on a catalog hit.
3. Every hard sensor, structure, provenance and health gate passes for the
   deployed sensor set; the generic 50-sensor projection/validation tests pass,
   while a real calibrated 50-station fixture remains separate evidence work.
4. The paired benchmark clears the declared speed and resource thresholds.
5. DayLibrary and exact per-day warming identities remain intact.
6. Production adoption, rollback and documentation are truthful and tested.

The initial evidence package reported all six criteria as passed on 2026-08-24.
A same-day robustness review found that its performance campaign compared a
12,000-candidate legacy arm with a 6,000-candidate catalog arm and that the
adoption record did not cross-bind reports, keys and stored bytes. Those first
claims are superseded by the matched-size campaign and its schema-v3
evidence-chain repair below.
Production now uses the verified catalog. Warming remains bound to exact
finished daily demand and the full annual population remains inactive.

## Robustness correction — 2026-08-24

- Both benchmark arms now receive the same explicit `--candidate-n-total`.
  Qualification rejects unequal requests, sizing drift, catalog keys or
  catalog sizes that differ from the supplied build report.
- Qualification records SHA-256 bindings for trial, build and suite evidence.
  Adoption cross-checks those bindings and verifies both immutable catalog
  entries before it can write schema v3. Runtime opens and hashes every named
  evidence file, cross-checks keys/sizes through the chain and verifies stored
  bytes again; stale/legacy-schema records select legacy, and a current-source key
  mismatch stops before implicit catalog use.
- Three-variant runs again bind seeds 1000/1001/1002 to q10/q50/q90, matching
  the annual warm plan and monthly decision contract.
- A widened continuous PFE rung can no longer leak tolerant counts into a
  route file. The publisher retries the exact no-bounds rung and otherwise
  fails closed; all sensor targets remain exact after whole-vehicle rounding.
- Mixed weekday/weekend catalogs namespace embedded `tour_id` values as well
  as vehicle IDs.
- Synthetic 50-sensor exact projection and output-validation tests establish
  that there is no fixed station-count cap. They do not substitute for the
  still-open real calibrated 50-station load/evidence campaign.

## Measured adoption result

The current release evidence is:

- `validation/route_catalog_invariance_v2_2026-08-24.json`: two fully bound dates
  produced identical canonical-template and neutral-route semantics.
- `validation/route_catalog_build_v2_2026-08-24.json`: both 6,000-template
  catalogs passed the first sizing attempt; total build cost was 28.137 s.
- `validation/route_catalog_trials_v2_2026-08-24.json`: 30 counterbalanced
  weekday, weekend, holiday and mixed-day pairs with the same 6,000-candidate
  request in both arms.
- `validation/route_catalog_qualification_v3_2026-08-24.json`: verdict
  `adopt`; median wall time 55.246→24.715 s (2.235x ratio of arm medians),
  median paired speedup 2.220x and paired saving 29.815 s, catalog
  p95 79.286 s versus legacy 150.383 s,
  adapter p95 0.678 s, maximum RSS 0.794 GiB and build amortization 0.944 days.
  Every hard gate and every day class passed, and paired vehicle population
  differed by at most 0.761%. The retained v2 trial rows predate the new
  distinct route×purpose workload counter; their PFE timings are reported but
  are not claimed as an isolated equal-work solver speedup.
- `validation/route_catalog_soak_v2_2026-08-24.json`: seven catalog builds plus
  explicit legacy rollback passed every hard gate. Catalog runs took
  16.86–62.35 s; the mixed two-day case was the maximum.
- The schema-v3 adoption record names and hashes the qualification/build files
  and follows their bound trials/suite evidence. It binds build SHA-256
  `13360c92…204115a`, weekday key
  `13020f80f1be36df59e27144aad8d808`, weekend key
  `b0426c8b73dd1201a0ea386bada9c45f` and exact size 6,000 for each.
- The active ordinary day was rebuilt through the default catalog path as
  build `ab27c11be5a6a8b52045`: 20,818 picker-declared vehicles, 672/672 exact
  integer sensor targets and zero residual. A three-seed baseline inserted all
  20,818 vehicles in every seed with zero waiting, unfinished trips,
  teleports or collisions.
- `validation/annual_warm_plan_v2_2026-08-24.json` and
  `validation/annual_warm_preflight_v2_2026-08-24.json` bind plan key
  `79a5c49c…1bbbae`; the preflight passed with one state worker. Exactly one q50
  warm-state unit for demand build `e72299dc2abeabcd` was then produced and
  verified (`succeeded=1`, `failed=0`, `pending=104684`). The live catalog day
  was restored afterward. This is a bounded pilot, not annual activation.

`validation_overall` remains `warn` for the separately documented external
trip-length-fit threshold. It was equal in meaning across arms and is not a
catalog hard failure. Every catalog and rollback build still had exact
directed sensor-edge × 15-minute integer targets and zero integer residual.

## Operating commands

All commands are dry-run or non-adopting unless `--execute` is supplied.

```bash
python3 tools/verify_route_catalog_invariance.py \
  --date-a 2027-09-08 --date-b 2027-09-09 \
  --out validation/route_catalog_invariance.json --execute

python3 tools/build_route_catalog.py \
  --pool-key both --report validation/route_catalog_build.json --execute

python3 tools/benchmark_route_catalog.py \
  --catalog-root sumo/route_catalog \
  --suite-gates validation/route_catalog_suite_gates.json \
  --out validation/route_catalog_trials.json --trials 30 \
  --n-total 6000 --execute

python3 tools/qualify_route_catalog.py \
  --trials validation/route_catalog_trials.json \
  --catalog-build validation/route_catalog_build.json \
  --out validation/route_catalog_qualification.json

python3 tools/adopt_route_catalog.py \
  --qualification validation/route_catalog_qualification.json \
  --catalog-build validation/route_catalog_build.json --execute

python3 tools/soak_route_catalog.py \
  --catalog-root sumo/route_catalog \
  --suite-gates validation/route_catalog_suite_gates.json \
  --out validation/route_catalog_soak.json --execute
```

The adoption command refuses any non-`adopt` report, false gate, mismatched
evidence hash, key, size or stored catalog byte. Without a valid schema-v2
`sumo/route_catalog_adoption.json`, the default fails safely to legacy. An
explicit `--candidate-source catalog` remains available for isolated evidence
generation; `--candidate-source legacy` is the rollback selector.

## Primary implementation files

- `traffic_sim/demand/route_catalog.py` — new small artifact/identity module
- `tools/build_route_catalog.py` — new thin isolated CLI
- `build_candidates.py` — expose canonical templates before daily resampling
- `build_sumo_demand.py` — orchestration and explicit daily margins
- `traffic_sim/demand/pfe.py` — accept explicit daily purpose margins
- `demand/day_library.py` — bind catalog keys in daily identity
- `traffic_sim/storage/singleflight.py` — reuse, do not duplicate
- focused catalog, demand, PFE, day-library and warm-state tests

`run_scenario.py`, closure ranking and SUMO warm-state semantics are consumers
of the finished daily release and should not need architectural changes for
this task.

## Primary references

- [SUMO routeSampler](https://sumo.dlr.de/docs/Tools/Turns.html): route sets are
  reused and selected to fulfill observed counts; route-file departure times
  are not the route-supply contract.
- [SUMO routes from observation points](https://sumo.dlr.de/docs/Demand/Routes_from_Observation_Points.html): a bounded whitelist of plausible routes
  is preferable in a highly meshed city network.
- [SUMO dynamic user assignment](https://sumo.dlr.de/docs/Demand/Dynamic_User_Assignment.html): congestion-dependent assignment requires iterative
  routing/simulation and is intentionally outside v1.
- [FHWA DTA calibration guide](https://ops.fhwa.dot.gov/publications/fhwahop13015/sec8.htm): count fit alone is insufficient; route choice and time-varying
  traffic behavior require separate validation.
