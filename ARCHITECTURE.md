# Architecture — structure locked 2026-07-05, status corrected 2026-08-21

**Product contract:** the city drops 15-minute count data (any number of
stations, directional or two-way) into the program and gets back (1) a
simulation of the measured period, (2) a simulation of any future date, and
(3) "what if we close these streets" — with a per-street statement of how
trustworthy each answer is and *where that number came from*. A reviewed added
station enters all three outputs without sensor-specific calibration code;
whether it actually improves held-out accuracy must be measured, not promised.

## The estimation hierarchy (the core idea)

Every quantity the program reports is produced at the highest possible level
of this hierarchy, and its provenance is carried all the way to the map:

1. **MEASURED** — a sensor's value. Never negotiated.
2. **MATHEMATICALLY DETERMINED** — flow conservation at junctions yields
   exact values where the system is locked, and intervals (min/max bounds)
   where it is not. Also yields consistency alarms between sensors.
   (Literature: link-flow observability, Castillo et al. 2015.)
3. **LEARNED PRIOR, INSIDE THE BOUNDS** — for what remains: the expected
   pattern for a street or direction at this hour. The current direction
   centre is a simple hour x day-type D-factor curve fitted from the tracked
   Nordic aggregate and pooled toward 0.5, with sensor 107 re-levelled to its
   published 2025 period aggregate. Its frozen Gate M is inconclusive because
   raw counts and independent day blocks are unavailable; q10/q90 are stress
   bands, not calibrated probabilities. The retired LightGBM model and data
   acquisition client are not production dependencies. This layer is a soft
   pull and yields before measurements. (An informative prior is required for
   identifiability — Marzano et al.)
4. **RECONCILIATION AND PUBLICATION** — continuous entropy/IPF and LP solves
   select route weights, followed by a joint integer projection that produces
   publishable vehicle counts. The ladder first tries to match level 1 inside
   its declared band, retain level-2 bounds and stay close to level 3. If these
   conflict, optional layers are surrendered in the executable order below and
   every relaxation is recorded. Hard publication infeasibility fails closed.
   This is a modern **Path Flow Estimator** informed by Bell & Shield 1996 and
   Chen et al. 2009, adapted to partial counts, inequality constraints and
   integer route publication.

Flows on unmeasured streets are the reconciled OUTPUT of level 4 rather than
measurements. They are influenced by candidate supply and level-3 priors, so
their trustworthiness is bounded and reported in stage F, not presumed.

**The hierarchy is enforced by the ORDER of the relaxation ladder** (`pfe.py`,
`solve_interval_with_relaxation`). When an interval is infeasible something
has to give, and which thing gives is the hierarchy in executable form:

```
RUNG_CLEAN             tol x1, everything on      everything holds
RUNG_NOBND_TOL1        tol x1, bounds OFF         level-2 bounds yield first
RUNG_NOQUOTA_TOL1      tol x1, + quotas OFF       then the purpose mix
RUNG_NOPRIOR_TOL1      tol x1, + priors OFF       then the level-3 priors
RUNG_LP_FALLBACK       tol x1, complete LP        IPF is not a completeness proof
RUNG_RELAX_TOL2X       tol x2, bounds on          the measured band widens
RUNG_RELAX_TOL4X       tol x4, bounds on          only after ALL of the above
RUNG_RELAX_NOBND       tol x4, bounds off
```

The invariant, stated once: **every non-measurement layer is surrendered
before the measurement band moves by one unit.**

CORRECTED 2026-08-06, three times in one day, each the same mistake in a
different layer — a constraint that stayed active at *every* rung, so when it
conflicted with a measurement the MEASUREMENT gave way:

1. **Level-2 bounds.** The ladder ran both tol-widening rungs before it would
   drop a bound, and had no rung dropping a bound at the unwidened band at
   all. Measured over 12 builds / 6,336 solves: 22.6% of intervals landed on
   `relax_no_bounds` (tol x4 AND bounds off) while the tol-widening rungs
   rescued 0.6%.
2. **Purpose quotas** (`required_groups`) — same shape, fixed by
   `RUNG_NOQUOTA_TOL1`.
3. **Level-3 priors** — the one that actually caused the weekend widened
   bands, fixed by `RUNG_NOPRIOR_TOL1`. On a real 2027-05-01 forecast build
   the opposite-carriageway priors at node 26355153 held two measured edges
   2-7 vehicles below target at a stable fixed point; dropping the layer
   recovered 12 of 12 intervals exactly. See `docs/OPEN_ISSUES_2026-08-06.md`
   section 6c.

`RUNG_LP_FALLBACK` also **moved above** the widening rungs. IPF is an
iterative scheme with no completeness guarantee, so its failure is not
evidence that no solution exists — the LP is what decides that, and while it
sat at the bottom the ladder could widen a band with an exact-band solution
still reachable. Moving it adds no capability (the position it left is
unreachable from anywhere the moved copy did not already run); it makes the
contract enforceable instead of aspirational.

**Two things make violations of this invisible, so do not rely on either.**
- **GEH cannot police the boundary.** The x4 band is ±max(8, 0.20·target); at
  its far edge GEH peaks at 3.81 for a 400 veh/quarter target, and the largest
  count ever measured on any of the 7 measured edges in any quarter is 203. A
  build can report 100% GEH<5 with a fifth of its intervals anywhere inside a
  20% band.
- **`tol_mult` never enters the IPF iteration.** It is read only by
  `_check_entropy_solution`, so a widening rung returns a bit-identical vector
  to its unwidened counterpart and merely judges it by a looser ruler. A
  widened band is therefore *never* evidence about the route pool — reading it
  as such is what produced the wrong diagnosis in 6c.

`relaxation_summary` in `demand_meta.json` is the diagnostic that does show
all of this, and `warn_widened_measurement_band` reports it on every build.

## The six pipeline stages

```
 data_in/ (city drops files)
    │
 [A INTAKE]        CSVs + coords + direction metadata → network.geojson, flows.json
    │
 [B MATHEMATICS]   conservation → exact values, intervals, consistency alarms
    │
 [C DESTINATIONS]  PFE-lite: candidates + hard counts + B's bounds + learned
    │              prior → vehicle routes + OD matrix     ← [D FORECAST] for
    │                                                        future dates
 [E SIMULATION]    SUMO meso Monte Carlo → flows everywhere, closures
    │
 [F CONFIDENCE]    provenance + leave-one-station-out error curve + MC spread
    │
 web/ + serve.py   (the window; interactive closures)
```

## Code organization

The repository keeps two deliberately different kinds of Python files
separate:

- **Root-level files are stable CLI entrypoints.** The build order, Makefile,
  run manifests and content fingerprints refer to commands such as
  `build_data.py`, `build_sumo_demand.py`, `run_scenario.py` and `serve.py`.
  Their paths are therefore treated as an interface and are not moved just
  to make the tree look tidy.
- **Reusable implementation lives in `traffic_sim/`.** Cross-cutting
  contracts and fingerprints are in `traffic_sim/core/`; sensor intake is in
  `traffic_sim/intake/`; demand calibration, the PFE solver/kernel and cache
  code are in `traffic_sim/demand/`; held-out validation and confidence
  reporting are in `traffic_sim/confidence/`; SUMO runtime, metadata, network
  audits and disruption metrics are in `traffic_sim/simulation/`; run/release
  bookkeeping is in `traffic_sim/ops/`.

The former root modules (`study_contracts.py`, `pipeline_fingerprint.py`,
`sensor_registry.py`, `candidate_cache.py`, `closure_metrics.py`,
`sumo_network_metadata.py`, `sumo_runtime.py`, `network_audit.py`,
`release_registry.py`, `runs.py`, `pfe.py`, `pfe_kernel.py`,
`validate_sim.py` and `validation_report.py`) are now thin compatibility
imports or CLI wrappers. They contain no second implementation. This preserves
existing scripts, tests and external command examples while giving new code
one canonical import location.
New reusable code must be added to `traffic_sim/` first; a root shim is added
only when an existing public command or import still requires it. Fingerprint
maps hash the canonical package files, so changing an implementation invalidates
the relevant cache or published build.

### Closure-integrity boundary

SINGLE-VEHICLE-CATEGORY PERMISSION FILTERING + PROVENANCE FIX 2026-08-29
(THIRD repair pass; addresses the two real gaps the SECOND pass below left
open, plus one more the same review found in `suggest_closure_time.py`).
`POLICY_VERSION` bumped `v2` -> `v3`.

Gap (a) — vClass/lane/connection legality. This project models exactly one
vehicle category (`traffic_sim.simulation.metadata.DEFAULT_VCLASS =
"passenger"`, SUMO's own implicit default for an undeclared `vType`/`vClass`
— verified directly: no `<vType>` or per-vehicle `type=` exists anywhere in
the production candidate pool or calibrated demand). The fix is NOT a new
per-trip vClass feature — the user explicitly ruled that out — it is making
the existing single-category graph provably legal: `build_metadata`
(schema bumped 1 -> 2) now computes, once, a `successors` adjacency already
filtered to `DEFAULT_VCLASS` (a connection only survives when the connection
itself and both lanes it joins permit the class, per SUMO's own `allow`-wins-
over-`disallow`, "declare nothing -> permitted" rule —
https://sumo.dlr.de/docs/Simulation/VehiclePermissions.html) and a
`restricted_edges` list for edges with zero legal lanes. `run_scenario.
build_edge_graph` — the single seam every production caller (`closure_routing`,
`suggest_closure_time.simulate_closure`, `monthly_sumo`'s adjacency) already
used — now sources its graph exclusively from this filtered index, on both
the cached and the XML-fallback path (same `metadata.build_metadata` call
either way, so there is no second, divergent parser). There is deliberately
no unfiltered variant exposed. On the current production network this
changes NOTHING (`net.net.xml` declares zero `allow`/`disallow` anywhere —
verified by direct inspection), which is the correct outcome: the fix makes
an already-true claim provable, it does not change today's routes. A
fixture test (`TestSingleVehicleCategoryLegality::
test_build_edge_graph_excludes_a_lane_restricted_to_another_class`, and
`test_reachability_respects_single_category_permissions` in
`test_scenario.py`, which used to document this as a "known limitation")
confirms a bicycle-only connection is now correctly excluded from the
passenger graph. Separately, `closure_routing.rewrite_route_file` now fails
closed (`ClosureRoutingError`, not a SUMO execution error) on any vehicle
fragment declaring a `type=` this policy cannot prove is `DEFAULT_VCLASS`
(`_check_vehicle_class`/`_compatible_vtype_ids`) — production data never
declares one, so this only ever fires on input the pipeline has not
produced before.

Gap (c), found by the same review pass in `suggest_closure_time.py`:
`closure_feasibility`'s own default argument is the legacy
`ct.CLOSURE_TIME_TO_TELEPORT_S = -1` ("disabled"), and both `monthly_sumo.py`
call sites omitted the argument — so every published `teleport_policy`
provenance record falsely claimed teleporting was disabled, when the actual
SUMO run (cold via `simulate_closure`'s own default, warm via
`_default_warm_invoker`'s explicit argument) was launched with
`closure_teleport.CLOSURE_ROUTING_TELEPORT_POLICY_S` (SUMO's own default,
teleporting ENABLED — safe now that closure_routing has already rewritten
every affected route around the closure before SUMO starts). Both call
sites now pass `closure_teleport.CLOSURE_ROUTING_TELEPORT_POLICY_S if
self.close_edges else None` explicitly, mirroring the value actually handed
to SUMO. `tests/test_closure_teleport_wiring.py::TestFeasibilityReporting`
was rewritten: the bare-default test now documents that the DEFAULT
argument is unchanged (still legacy/disabled, since a function's own
default is a separate fact from what production passes), a new test proves
the production value reports `teleporting_enabled: True`, and a source-grep
test pins that both `monthly_sumo.py` call sites pass it explicitly. Teleport
and closed-edge-throughput hard-failure gates are unaffected either way —
this only fixes whether an absent "teleports" failure counts as evidence.

PROVENANCE, gap (b)'s partial fix completed: the free-form
`routing_provenance` dict from the SECOND pass is now a validated
`closure_routing.RoutingProvenance` dataclass (fail-closed `__post_init__`,
strict `from_dict`/`to_dict`) binding `routing_policy_version`,
`vehicle_class`, `candidate_id`, `work_date`, `demand_variant`, `seed`,
`execution_arm`, `access_impact_sha256` (resolvable to the full per-vehicle
access-impact report, itself already content-addressed in the monthly
cache's `access-impact/` store), `rerouted_around_closure`, and
`denied_count`. `RoutingProvenance.from_dict` requires the field set to
match EXACTLY, so a legacy dict (missing `vehicle_class`/`denied_count`/etc.)
is rejected rather than silently backfilled — the same "old evidence must
never satisfy a new lookup" rule `POLICY_VERSION` already enforces, applied
to this one provenance shape. `TestRoutingProvenance` (tamper: wrong policy
version, wrong vehicle class, negative counts, invalid execution arm;
incompatible-cache: legacy field set, unexpected extra field) pins this.
NOT DONE, still: this stops short of threading routing identity into
`PairedObservation`/`CandidateEvidence`/`CanonicalObservationDigest`
(`finalist_decision.py`) or bumping their own schemas — `CanonicalObservation
Digest.from_dict` enforces an exact field set against 16 separately frozen
`validation/monthly_warm_state_manifest_v*.json` artifacts, and widening
that surface remains judged too large/risky for a repair-batch pass, same
call as the SECOND pass. The `routing_provenance` dict folded into
`build_monthly_observation`'s free-form `provenance` mapping (not the frozen
digest schema) is the additive route taken instead, exactly as before.

DURABILITY FIX 2026-08-29 (FOURTH pass, a genuine defect this dataclass-
threading scope decision had been quietly masking): re-checking gap (b)'s
"partial fix" against actual runtime behaviour found it was not merely
partial, it was practically unresolvable. `ArchivedDemandSumoRunner.run_candidate`
reset `self.canonical_observations = []` at the top of every call and never
wrote the assembled canonical payload anywhere durable, so
`CanonicalObservationDigest.sha256` — the only trace `CandidateEvidence`
retains of a canonical observation, including its `routing_provenance` —
named a payload that existed nowhere by the time any reader could ask for
it. Fixed WITHOUT touching any of the 16 frozen dataclass schemas:
`ArchivedDemandSumoRunner._preserve_canonical_observation`/module-level
`resolve_canonical_observation` persist and re-read the canonical payload
content-addressed under `cache_root/canonical-observations/<sha256[:2]>/
<sha256>.json`, mirroring the existing `_preserve_access_impact_evidence`
pattern exactly (write-once on first occurrence, fail closed on a missing
file via `CanonicalObservationNotFound`, fail closed on a tampered one via
`CanonicalObservationTampered`). `run_candidate` now persists before
recording each digest, so the digest a reader holds is guaranteed
resolvable back to the full payload — routing provenance, the access-impact
digest it in turn names, feasibility, recovery, and both arms' health
metrics. `tests/test_monthly_sumo.py` gained a round-trip test (the exact
persisted payload comes back byte-for-byte equal), a write-once dedup test,
and both fail-closed tests.

REMAINING, NOT DONE: threading routing identity directly into the frozen
`PairedObservation`/`CandidateEvidence`/`CanonicalObservationDigest` field
sets themselves, same judgement call as the SECOND and THIRD passes.

FROZEN-UNIT HARNESS REPLAY — DONE 2026-08-29 (FOURTH pass). The THIRD
pass's BLOCKED verdict ("this sandboxed environment has no `sumo`/
`netconvert`/`duarouter` binary and no `sumolib`") was itself wrong: it
tested `which sumo`/a bare `import sumolib` instead of this repository's
own runtime contract, `traffic_sim.simulation.runtime.sumo_home()`, which
resolves a real, working Eclipse SUMO 1.27.1 install
(`/Users/gt/Library/Python/3.9/lib/python/site-packages/sumo`, the
`eclipse-sumo` pip package) on this host. `tools/verify_closure_routing_
frozen_units.py` reads the original `ui-monthly-12hg8f3` ledger read-only,
reconstructs each named unit's own one-day `ClosureSchedule` from
`units.ndjson`, independently re-derives its `unit_id` via `independent_
daily.decompose_schedules` and asserts it matches the ledger, then drives
the REAL `MonthlyDemandResolverRunner` (`build_missing=False`) wrapped in
`IndependentDailyRunner` (`queue_workers=1`) with fresh exclusive release/
baseline-cache/daily-cache/report roots under `--output-root` (refuses to
run if that root already exists; no path reaches `run_monthly_search` or
campaign orchestration). Measured, real SUMO, real 2027-09-28 forecast
demand: `daily-unit-24737391111be0e137537df7` (the unit earlier diagnostic
replays under the OLD truncate-and-runtime-reroute policy had found timing
out) — q10/seed 1000 first-attempt wall time **29.72 s** (limit 300 s), 0
denied, 0 teleports, no closure leak, `recovered: True`; q50/q90 equally
clean. Healthy control `daily-unit-2387bbad11130660b9de0d17` (same closed
edge, 00:00-08:00 window) clean on all three variants too, 0 invented
denials. Every `routing_provenance` record resolved end to end through the
new durability fix above, on real data. See AGENT_NOTES.md CURRENT_HANDOFF
for the FOURTH pass's complete record. Its disclosed scope limit on the
byte-identical-unaffected-fragment check (whole-file digest equality only
proves anything when nothing at all was rerouted) is CLOSED by the FIFTH
pass below.

DURABLE PROVENANCE + REAL PER-VEHICLE VERIFICATION -- DONE 2026-08-30
(FIFTH pass, continuing from a fresh review that used the corrected SUMO
resolution above to run the FOURTH pass's own replay tool for real and
found three concrete defects the FOURTH pass's static review had not
exercised). All three repaired in one pass, all three re-verified with a
fresh real-SUMO replay of both frozen units in a new exclusive root
(`/tmp/closure-routing-verify-run3`, disposable, outside the repo; prior
roots `-run1`/`-run2` preserved unchanged as history):

1. `active_closed_edge_throughput` READ `None` on every variant of both
   frozen units despite `measured_empty_edges` forcing a zero-filled
   series to exist -- not a report of "never measured", a real indexing
   bug. `metrics.active_closure_throughput` computed `first_full`/
   `end_full` as ABSOLUTE quarter indices from the closure's own
   epoch-relative `begin_s`/`end_s`, but `flows` (from `parse_edgedata`)
   is indexed from 0 at whatever second the SUMO run itself started with
   `--begin` -- a trimmed independent-daily cold window starts at the work
   day's own midnight offset from the archive epoch, not at epoch zero, so
   every closure quarter index landed past the end of the (correctly
   zero-filled but short) array and `measured` never turned `True`. Fixed
   with a new `window_begin_s` parameter (default 0, preserving every
   existing whole-day caller) at all three real call sites
   (`monthly_sumo.py`'s warm post-segment, `suggest_closure_time.
   simulate_closure`'s cold `run_one`, both threading their own actual
   run `begin_s`). MEASURED after the fix: both frozen units now report
   `active_closed_edge_throughput: 0` (numeric, proven clean) on all six
   (unit, variant) observations, replacing every prior `null`. Pinned by
   `tests/test_closure_metrics.py::
   test_active_closure_throughput_indexes_relative_to_a_trimmed_window`
   (reproduces the exact bug shape unfixed, proves the fix) and a sibling
   real-flow test.
2. `RoutingProvenance` (`closure_routing.py`) bound no `unit_id` (only
   `candidate_id`/`schedule.schedule_id` -- a daily-unit's OWN identity, as
   monthly ledgers and this repair batch's own acceptance criteria key
   evidence by, was unrecoverable from a published observation) and no
   `transformed_route_sha256` (the actual route SUMO ran was only
   resolvable indirectly by resolving the whole access-impact report's
   `output_route_sha256`); `access_impact_sha256` was `str | None` and
   validated only by LENGTH, not hex content; and two `monthly_sumo.py`
   call sites synthesized a fake zero-valued `RoutingProvenance`
   (`access_impact_sha256=None`, invented `0` rerouted/denied) whenever a
   result dict was missing the real one or the cold arm produced no
   routing records -- publishing "no routing happened" evidence for an
   observation that certainly had one, rather than surfacing the defect.
   Fixed: `RoutingProvenance` gained `unit_id` (derived once, inside
   `ArchivedDemandSumoRunner._unit_identity`, via the SAME
   `independent_daily.decompose_schedules` the ledger itself used to mint
   the id -- a mismatch is a hard error, never a silent drift; falls back
   to the schedule's own id for a non-independent-daily schedule, which
   has no daily-unit concept at all) and `transformed_route_sha256`
   (computed the moment the rewritten route file is written, mirroring
   `write_access_impact_report`'s own `output_route_sha256`); both digest
   fields are now REQUIRED and validated against `_HEX64` (lowercase hex,
   not merely length 64); both synthetic fallbacks were replaced with an
   explicit `ClosureRoutingError` raise, because `close_edges` is never
   empty for a real closure observation, so an empty result is a defect,
   not a legitimate state to paper over. Both transformed-route and
   access-impact evidence are now preserved DURABLY, content-addressed
   under the monthly cache (`_preserve_transformed_route`, mirroring
   `_preserve_access_impact_evidence` exactly), and BOTH preservation
   methods now validate an existing destination's bytes against its own
   filename before reuse instead of trusting presence alone (a duplicate
   dead `return digest` in `_preserve_canonical_observation` was also
   removed while adding the same validation there). `POLICY_VERSION`
   bumped `closure_origin_routing_v3` -> `v4` (documented in the module's
   own version-bump ladder), so no v3 evidence can satisfy a v4 lookup.
   New/extended tests in `TestRoutingProvenance` (missing `unit_id`, null
   digests, non-hex digests, uppercase digest, short digest, all rejected)
   and a new `ClosureRoutingResult` consistency check
   (`rerouted == len(rerouted_vehicle_ids)`, see point 3).
3. `tools/verify_closure_routing_frozen_units.py` reported
   `unaffected_route_check.byte_identical_to_source: null` whenever
   ANYTHING was rerouted or denied (i.e. every real observation -- both
   frozen units always reroute thousands of vehicles), because it only
   ever compared whole-route-file digests, which cannot distinguish "one
   vehicle changed" from "everything changed". Fixed at the root:
   `ClosureRoutingResult`/`rewrite_route_file` now also name every
   REROUTED vehicle id (`rerouted_vehicle_ids`, mirroring the existing
   `access_impact` list of DENIED ids), written into the access-impact
   report (`schema_version` 1 -> 2, additive field); the transformed route
   file is now durably preserved (point 2); and the tool independently
   parses both the read-only source route (resolved via `resolved.
   archive_for(schedule) / VARIANT_FILENAMES[variant]`, reusing
   `closure_routing`'s own vehicle-fragment regexes so the check cannot
   silently disagree with the parser that produced the file) and the
   preserved transformed route, then BYTE-DIFFS every vehicle NOT named
   rerouted or denied. A new `_healthy_control_semantic_check` also
   reports an explicit selected-field comparison (no denied trips, no
   hard failures, no teleports on either arm, every unaffected vehicle
   byte-identical) whenever an observation denies nothing -- gated on
   `denied_count == 0` alone, not on zero reroutes, since rerouting
   thousands of vehicles around a real closure is the expected, healthy
   outcome the acceptance criteria describe, and only a fabricated DENIAL
   would mean the policy invented lost access.
   MEASURED, fresh real-SUMO replay, both units, all three variants (18
   observations total across `-run2`/`-run3`): `daily-unit-
   24737391111be0e137537df7` (the former-timeout unit) first-attempt wall
   times 30.94 s / 26.45 s / 25.36 s (q10/q50/q90, limit 300 s), 0
   denied, 0 teleports on either arm, `active_closed_edge_throughput: 0`,
   `recovery.recovered: True`, 55,633-55,774 unaffected vehicles per
   variant checked and BYTE-IDENTICAL to source (0 mismatched, 0 missing),
   `healthy_control_semantic_check.all_passed: True`. Healthy control
   `daily-unit-2387bbad11130660b9de0d17` (same edge, 00:00-08:00 window):
   23.70-26.62 s, identical clean shape, 57,388-57,501 unaffected vehicles
   byte-identical per variant. Both units' `unit_id` re-derivation matched
   the ledger exactly. Source workspace `ui-monthly-12hg8f3` and prior
   replay roots `-run1`/`-run2` verified unchanged (no git diff, no mtime
   change) after this pass.
   Focused+broader test bundle re-run clean (857 passed, 2 skipped,
   spanning closure/monthly/finalist/independent-daily/suggest-closure-
   time/deterministic-disruption/access-impact/serve/cost-ordered/daily-
   queue). Frozen-manifest suites show the same class of failure as every
   prior pass (source-digest drift from `POLICY_VERSION` v3->v4 and the
   other hashed-file changes above), confirmed pure digest mismatch, not
   behavioural -- not touched or regenerated.

DURABLE EVIDENCE VALIDATION + REAL HEALTHY-CONTROL COMPARISON -- DONE
2026-08-30 (SIXTH pass, repairing review-03's CHANGES_REQUIRED verdict on
the FIFTH pass; two findings, both concrete, both repaired without altering
the routing architecture itself):

1. `IndependentDailyRunner._load_cached` validated only the daily-cache
   envelope and `CanonicalObservationDigest` identity fields on a cache
   hit; `monthly_search.evidence_from_dict`/publication validated only JSON
   shape. Neither ever RESOLVED the nested canonical payload, its
   `RoutingProvenance`, or the access-impact/transformed-route artifacts
   that provenance names -- so a missing, tampered, or swapped/aliased
   durable artifact behind an otherwise well-formed cache entry or
   published record could be accepted as valid evidence. Fixed with one
   shared function, `monthly_sumo.validate_canonical_observation_evidence`
   (plus `resolve_access_impact_report`/`resolve_transformed_route`, new
   read-side siblings of `resolve_canonical_observation` with the same
   fail-closed missing/tampered contract), which also checks that the
   resolved `RoutingProvenance`'s own `(candidate_id, work_date,
   demand_variant, seed)` matches the `CanonicalObservationDigest` record
   naming it -- closing a swapped-evidence path neither prior check could
   see. Wired at FOUR points so cache reload, fresh writes, resume, and
   publication can never disagree about what "valid evidence" means:
   `IndependentDailyRunner._load_cached` (reload) and `_save_cached`
   (fresh-write -- validated symmetrically, so a backend defect is caught
   at write time rather than only rediscovered on the next reload) via a
   new `_canonical_evidence_cache_root()` accessor (duck-typed:
   `self.daily_runner.cache_root`, falling back to
   `self.daily_runner.delegate.cache_root` for the `IsolatedDailySumoRunner`
   process-isolation wrapper -- this is deliberately NOT the same root as
   `self.cache_root`, which is the daily-evidence cache, not the SUMO
   backend's durable-artifact store); `monthly_search.evidence_from_dict`
   (resume) and `_run_and_publish_candidate` (publish) via a matching
   `_evidence_cache_root(runner)` accessor that duck-types
   `runner._canonical_evidence_cache_root`, so `monthly_search.py` still
   never imports the heavy `monthly_sumo` module at load time (only inside
   the validation call itself, lazily). Both accessors return `None` for a
   backend/test double exposing no cache root, in which case validation is
   skipped exactly as before -- every pre-existing lightweight
   `FakeDailyRunner`-based test stayed green unmodified. 11 new tests: 6 in
   `test_monthly_sumo.py` exercising `validate_canonical_observation_evidence`
   directly against a real `ArchivedDemandSumoRunner`-built chain (complete
   chain accepted; missing canonical observation; missing
   `routing_provenance`; tampered access-impact report; missing transformed
   route; identity-mismatch/swapped-evidence), 3 in `test_independent_daily.py`
   via a `FakeDailyRunnerWithCacheRoot` double that exposes a real backend
   cache root (happy-path round trip through a genuine durable chain;
   `_save_cached` fails closed on a phantom digest, confirmed nothing is
   written to the daily cache; `_load_cached` fails closed and bumps
   `cache_corrupt` on a tampered canonical observation), 2 in
   `test_monthly_search.py` (`evidence_from_dict` fails closed given a
   `cache_root` and an unresolvable digest; `_evidence_cache_root`'s
   duck-typing itself).
2. `tools/verify_closure_routing_frozen_units.py`'s
   `_healthy_control_semantic_check` checked only the CURRENT run's own
   invariants (no denials/teleports/hard-failures, byte-identical
   unaffected vehicles) with no reference observation at all, and applied
   to EVERY zero-denial observation -- which could label the former-timeout
   unit a "healthy control" on whichever variant happened to deny nothing,
   even though it is not the designated control. Fixed: membership now
   comes from a frozen `HEALTHY_CONTROL_UNIT_ID =
   "daily-unit-2387bbad11130660b9de0d17"` constant checked by identity, so
   the former-timeout unit can never qualify regardless of its own
   per-variant outcome. A new optional `--reference-report` flag (the tool
   stays fully read-only -- it is loaded, never written to) points at a
   prior `frozen_unit_verification.json`; when supplied, the healthy
   control's check additionally compares an explicit 8-field allowlist
   (`HEALTHY_CONTROL_REFERENCE_FIELDS`: denied count, hard failures, both
   teleport totals, active closed-edge throughput, and the three
   unaffected-route-check fields) against the matching unit/variant in that
   reference report, recording `{current, reference, equal}` per field plus
   the reference report's own path and sha256, with an explicit note that
   content-addressed digests, wall-clock timing, launch telemetry, and
   `routing_policy_version` are excluded as expected drift reported
   elsewhere, not a health signal.
   MEASURED, two fresh real-SUMO replays through the real
   `MonthlyDemandResolverRunner`/`IndependentDailyRunner` path
   (`runtime.sumo_home()` resolved the same Eclipse SUMO 1.27.1 as every
   prior pass), each in its own exclusive fresh root, both preserved:
   `runs/closure-routing-verify-20260830-001/` (no `--reference-report`) --
   former-timeout unit `daily-unit-24737391111be0e137537df7`: 32.0/28.4/
   27.1 s first-attempt (q10/q50/q90, limit 300 s), 0 denied, 0 teleports,
   `active_closed_edge_throughput: 0`, 55,633-55,774 unaffected vehicles
   per variant byte-identical to source, `healthy_control_semantic_check:
   None` on every variant (correctly excluded by identity, confirming the
   fix). Healthy control `daily-unit-2387bbad11130660b9de0d17`: 28.5/25.5/
   24.7 s, 0 denied/teleports, 57,388-57,501 byte-identical vehicles per
   variant, `all_passed: True`, `reference_comparison: None` (no prior
   report existed -- this run became the reference). `runs/closure-routing-
   verify-20260830-002/` (`--reference-report` pointed at the first
   report): reproduced the same clean shape (24.8-32.0 s across both
   units), and for the healthy control on every variant
   `reference_comparison.all_equal: True` across all 8 fields, with the
   reference path and sha256 (`907e809c...`) recorded -- confirming the
   comparison resolves and checks real prior evidence end to end, not a
   mocked one. The former-timeout unit's `healthy_control_semantic_check`
   again stayed `None` on every variant. Both units' independently
   re-derived `unit_id` matched the ledger exactly in both runs. Source
   workspace `ui-monthly-12hg8f3` and every prior replay root (PASS 4's
   `-run1`/`-run2`/`-run3` plus this pass's own `-001`) verified unchanged.
   Focused+broader test bundle re-run clean (855 passed, 1 skipped).
   Frozen-manifest suites show the same 19 pre-existing failures as PASS
   3-5 (this pass's edits to `monthly_search.py`, `monthly_sumo.py` and
   `independent_daily.py` are all already-drifted frozen sources from PASS
   4/5's `POLICY_VERSION` v3->v4 bump), confirmed pure digest mismatch --
   not touched or regenerated.

STRICT EVIDENCE BINDING + FAIL-CLOSED REPLAY VERDICT -- DONE 2026-08-30
(SEVENTH repair pass, closing the three findings in ai-flow review-03):

1. Hash resolution is no longer treated as semantic validation. Access-impact
   reports now use schema version 3 and `closure_routing.validate_access_impact_
   report` requires the exact schema/policy, complete unit/schedule/date/
   variant/seed/arm/vehicle-class identity, canonical closure fields, valid
   denial reasons, unique/disjoint rerouted and denied ids, and internally
   consistent denied/rerouted/unaffected populations. The report's output-route
   digest must equal `RoutingProvenance.transformed_route_sha256`, and its kept
   population and ids must match the hash-resolved transformed route itself.
   `monthly_sumo.validate_canonical_observation_evidence` applies this check on
   fresh cache writes, reload, monthly resume and publication. Hash-valid but
   swapped reports now fail closed.
2. `tools/verify_closure_routing_frozen_units.py` report schema v2 preserves the
   exact identity-bearing launch records added by each unit/variant call and
   derives first-attempt, retry, timeout and final-outcome state from them. A
   top-level `verification_status`/`all_passed` verdict requires both named
   units, all q10/q50/q90 variants, exactly one successful non-timeout launch
   below 300 seconds, zero denials/teleports/closed-edge throughput/hard
   failures, proven recovery, byte-identical unaffected vehicles and (when a
   reference is supplied) equal healthy-control fields. Any unmet criterion
   returns exit code 1.
3. The marked current blocks in `TASKS.md` and `AGENT_NOTES.md` now each contain
   one concise current snapshot. Superseded pass detail remains preserved under
   explicit history markers outside those current blocks.

A fresh real-SUMO replay in the additive root `runs/closure-routing-verify-
20260830-003/`, using `-002` read-only as its reference, returned
`verification_status: passed` and `all_passed: true`. All six exact records
were attempt 1 / `success`; wall times were 24.8-30.6 seconds, with zero
timeouts, retries, denials, teleports or closed-edge throughput, recovery true,
and unaffected routes byte-identical. Focused tests, the broader closure/monthly
surface, targeted pylint and `git diff --check` passed. The frozen-manifest
audit retained the same 19 already-recorded source-drift/retired-fixture
failures; no frozen evidence was rewritten and no campaign was launched.

CANONICAL COVERAGE + STORE BINDING HARDENING -- IN REVIEW 2026-08-30
(EIGHTH repair pass, responding to `review-independent-01`):

1. Candidate-evidence schema v4 serializes an explicit
   `canonical_evidence_store_v1` absolute root whenever canonical digests are
   present. Publication and resume require that reference to match the active
   backend; every digest is resolved through it. Successful observations must
   have exact `(variant, seed)` digest coverage, and independent-daily parent
   evidence must additionally contain the full expected daily-schedule/unit
   launch matrix. Resolved canonical payload schedule/variant/seed/arm fields
   and all three denial-count representations must agree with strict
   `RoutingProvenance`.
2. Access-impact reports with the complete seven-field monthly identity retain
   strict durable schema v3. Generic/no-identity/multi-seed reports now carry
   the separate `closure_access_impact_diagnostic_v1` kind and can therefore
   never be mistaken for strict monthly evidence.
3. Frozen-unit replay timing is valid only when all duplicated timing fields
   are finite, positive, below 300 seconds and exactly equal; NaN, infinity,
   zero, negative and inconsistent values fail the top-level verdict.

Focused manipulation tests and the broad closure/monthly/server/benchmark
surface passed (`887 passed, 1 skipped`). Fresh additive real-SUMO replay
`runs/closure-routing-verify-20260830-004/` passed all six first attempts in
26.0-33.1 seconds with zero retries, timeouts, denials, teleports or
closed-edge throughput and equal healthy-control reference fields. Final
`review-independent-02` confirmed those three findings closed, then found one
medium concurrency defect in deterministic `.tmp` names used by the
content-addressed route/access publishers. Both publishers now stage through
unique same-directory files and atomically replace the shared immutable
destination. A barrier-driven eight-worker regression test forces all writers
past copy before rename for both stores. Post-fix broad checks pass (`889
passed, 1 skipped`), and additive real-SUMO replay `-005` passed all six first
attempts in 26.1-31.9 seconds with the same clean health/reference result.
Final `review-independent-03` returned `APPROVED` with no findings after
independently resolving all 12 durable chains across `-004`/`-005` and checking
the concurrency repair. No campaign or frozen evidence was changed.

TIMING-INVARIANT FIX 2026-08-29 (SECOND repair pass; supersedes the
"ARRIVAL-UNCERTAINTY FIX" paragraph immediately below, whose own margin was
found unsound on a second review). The 900 s additive margin that paragraph
describes could never PROVE a vehicle clears an edge before a still-open
closure ends: congestion delay has no demonstrated upper bound, so a vehicle
whose real transit landed more than 900 s later than its free-flow estimate
would still have been classified "not applicable" and handed to SUMO on its
original, closure-crossing route — the exact failure class this whole module
exists to remove. `_closures_overlapping` (`traffic_sim/simulation/
closure_routing.py`) now uses the one interval fact that IS provable without
bounding congestion at all: real transit is never faster than free flow (SUMO
only ever adds delay relative to free speed, never subtracts it), so
`depart_s + free_flow_elapsed` is a true LOWER BOUND on when a vehicle can
occupy an edge. A closure window is provably missed only when that lower
bound has already reached or passed the window's `end_s` — real, slower
transit only pushes the true arrival further past it, never back into the
window. Every other case — including a window still far in the future,
which the old margin would have called safe — has no available proof and is
now classified applicable/affected. This can only WIDEN who is treated as
affected relative to the retired margin, never narrow it.
`rewrite_route_file`'s post-detour residual check still calls this same
predicate on the final route; that is legitimate here (unlike reusing an
unproven margin to "prove" itself) because the predicate is now a real
one-directional proof, not a heuristic guess — checking it again on the
final route is genuine defence-in-depth against the fixed point failing to
converge correctly, not circular reasoning. `POLICY_VERSION` bumped
`closure_origin_routing_v1` -> `v2`, since this can change which trips are
unaffected/rerouted/denied for the same input relative to v1.

SAME PASS, second real fix: `destination_closed` used to deny a trip the
instant its destination edge appeared anywhere in the closed-edge set,
without checking whether THAT destination's own closure window actually
applies to that trip's arrival — denying trips that arrive long before a
future closure or well after a past one has reopened. `rewrite_route_file`
now computes applicability once per vehicle and denies `destination_closed`
only when the destination is itself in the applicable set; an
otherwise-unaffected destination trip is preserved byte-for-byte. Pinned by
`tests/test_closure_routing.py::TestWindowedDestinationDenial` and the
rewritten `TestClosureTimingInvariant` (replacing the retired
`TestArrivalUncertaintyMargin`).

PROVENANCE, same pass (partial): `write_access_impact_report`/
`prepare_route_file`/`reroute_closure_affected_vehicles` gained an optional
`identity` parameter (candidate/schedule id, demand variant, seed, work
date, execution arm), written verbatim into the access-impact evidence file,
wired at all three production call sites. The access-impact-report sha256
that was already being computed at both `monthly_sumo.py` call sites (warm
`_preserve_access_impact_evidence`, cold `simulate_closure`'s
`replication_records`) was previously discarded; both now fold a
`routing_provenance` dict (policy version, access-impact sha256, rerouted
count) into `build_monthly_observation`'s `provenance` mapping. Because
`closure_routing.py`'s own source bytes were already part of
`simulation_source_digest`, the v1->v2 semantic change already invalidates
every pre-fix monthly cache/backend identity with no further plumbing
required — visible directly as the pre-existing frozen-manifest suites
(`test_monthly_warm_state_freeze.py`, `test_monthly_warm_state_v16_freeze.py`)
now failing on source drift, which is the intended consequence of a real
change to a hashed production file, not a regression; those frozen
manifests were not touched or re-generated.

REMAINING, NOT DONE in this pass, both real gaps: (a) routing still has no
vehicle-class/lane/connection-permission awareness — `build_edge_graph`,
`disruption.shortest_path_edges` and `closure_routing` route on an
unqualified successor graph, so a "fastest legal path" claim is not
currently provable for a restricted vClass; (b) the literal frozen-unit
harness replay (`daily-unit-24737391111be0e137537df7` /
`daily-unit-2387bbad11130660b9de0d17` through the real monthly-search
worker path) is still not built, exactly as the paragraph below already
recorded before this pass.

ARRIVAL-UNCERTAINTY FIX 2026-08-29 (repair pass over the root-cause fix
below, after review — SUPERSEDED by the TIMING-INVARIANT FIX above; kept
verbatim as history, do not treat its margin as current): `_closures_
overlapping`'s window check compared a
single FREE-FLOW arrival instant against `[begin_s, end_s)`. Free-flow travel
time is a LOWER bound on real, congested transit time — congestion only
delays, never speeds up — so a vehicle whose optimistic estimate lands just
outside a window boundary is not proof its real transit does; this is
exactly what happened to the boundary-timing vehicle recorded below (arrival
within roughly a minute of the window's edge, left unrouted, later
jam-teleported onto the closed edge). Fixed: the check now uses the edge's
OCCUPANCY INTERVAL `[arrival, arrival+edge_travel_s]`, not a point, padded on
both sides by `CLOSURE_TIMING_SAFETY_MARGIN_S = 900` (15 min) — padding can
only widen who counts as affected, never narrow it. `rewrite_route_file` also
now re-checks every rewritten route against `_closures_overlapping`
immediately after planning and raises `ClosureRoutingError` on any residual
overlap, so the fixed point's termination is proven per vehicle, not merely
trusted. Pinned by `tests/test_closure_routing.py::
TestArrivalUncertaintyMargin` (5 tests, including an end-to-end
`rewrite_route_file` reproduction of the near-boundary miss shape). NOT YET
RE-RUN: the exact real-evidence incident this fixes (edge
`96527131_26842526_0`, 07:15-15:15, 2027-11-11 demand) was not replayed
end-to-end through SUMO in this pass — the fix is verified at the unit/file
level, matching the incident's boundary-timing shape directly, not by a fresh
real-SUMO run of that specific case. The literal frozen-unit harness replay
(`daily-unit-24737391111be0e137537df7` / `daily-unit-2387bbad11130660b9de0d17`
through the real monthly-search worker path) also remains unbuilt — no code
in this repository references either unit id.

SUPERSEDED 2026-08-29 (root-cause fix; the `--time-to-teleport -1` policy
below is retired from production). ROOT CAUSE of the monthly-search wall-time
timeouts (`ui-monthly-12hg8f3`, 540/5,180 exact launches hit the 300 s
limit): four distinct things, only the last of which was visible.
(1) PREPROCESSOR: the old preprocessor (`truncate_stranded_vehicles`) only
ever rewrote the narrow "no detour exists at all" case; every vehicle WITH a
detour kept its original, closure-crossing route into SUMO. (2) LOCAL
REROUTER TIMING: SUMO's `<rerouter>`/`closingReroute`
(https://sumo.dlr.de/docs/Simulation/Rerouter.html) only replans a vehicle
when it reaches one of the rerouter's own trigger edges (400 m of the
closure), so every affected vehicle's replanning was concentrated at the
closure's doorstep instead of spread across its route from departure
(https://sumo.dlr.de/docs/Simulation/Routing.html). (3) DISABLED GRIDLOCK
HANDLING: `CLOSURE_TIME_TO_TELEPORT_S = -1` suppressed SUMO's stuck-vehicle
relief network-wide, for the whole run, not just the closed edge
(https://sumo.dlr.de/docs/Simulation/Why_Vehicles_are_teleporting.html); under
(2)'s doorstep-concentrated replanning a busy closure's queue could then grow
without the only mechanism that could ever clear it. (4) WALL-TIME TIMEOUT
was the SYMPTOM: the 300 s subprocess timeout is what actually stopped a run
stuck in (3) — a diagnostic replay showed running/halting vehicle counts
growing without bound during the active closure.

THE FIX: `traffic_sim/simulation/closure_routing.py`
(`POLICY_VERSION = "closure_origin_routing_v1"`) rewrites every affected
vehicle's route BEFORE SUMO starts, from its original origin to its original
destination, along the deterministic fastest legal path with every
applicable closed edge excluded (a fixed point: closures overlapping the new
path's own transit are re-checked and the banned set grows, monotonically,
until it stabilizes — provably terminating within `len(close_edges)+1`
rounds). It uses the same shortest-path engine
(`traffic_sim/simulation/disruption.py::shortest_path_edges`) that also
prices closure severity, so routing and ranking can never disagree about
reachability. A trip is held outside the network (never simulated, never
truncated, never left waiting) ONLY when its original destination sits on a
closed edge, or no legal detour exists at all once the closure(s) are
excluded; each such denial is recorded as a stable, provenance-bound
`AccessImpactRecord` (reason, vehicle id, original endpoints, closed-edge
set, policy version, route/network digests) — never as successful traffic,
never as a generic timeout. `run_scenario.reroute_closure_affected_vehicles`
is the sole production entry point used by `run_scenario.py`'s scenario
path, `suggest_closure_time.simulate_closure` (the monthly campaign path) and
`ArchivedDemandSumoRunner` (both cold and warm-audit); `truncate_stranded_
vehicles` is retired to a historical-diagnostic-only helper, pinned
unreachable from all three by tests. `write_closure_additional`'s runtime
`<rerouter>` is retained as a fail-closed structural declaration (defense in
depth against a routing defect) but is no longer the mechanism that produces
correct detours. Closure runs no longer need a disabled teleport threshold:
production now defaults to `closure_teleport.
CLOSURE_ROUTING_TELEPORT_POLICY_S` (`None`, i.e. SUMO's own default, exactly
like a baseline run) — safe because the closure hazard is eliminated before
simulation begins rather than suppressed during it, and it restores teleport
count as a genuine health signal. `CLOSURE_TIME_TO_TELEPORT_S = -1` is kept,
named, for historical diagnostic reproduction only.

MEASURED (2026-08-29, `run_scenario.py --closure` against the live
2027-11-11 demand, same closed edge as the timed-out unit
`96527131_26842526_0`, 8 h window, single seed): a clean run (window shifted
off one adjacent boundary case — see below) completed in **10.05 s total wall
time** (SUMO execution itself 1.95 s), 1,803 vehicles rerouted, 0 denied, 0
teleports, `closure_integrity: verified_clean`, scenario published — against
the previous 300 s first-attempt timeout / 743.837 s registered-retry
completion for this same edge. A second run at the timed-out unit's own
literal window (07:15–15:15) completed in 9.95 s but was correctly refused
publication by the pre-existing `closure_integrity_status` fail-closed gate:
one vehicle (`pfe12313`) teleported (SUMO `jam` reason) onto the closed edge.
Verified via `git stash` that this exact single-vehicle event reproduces
**identically under the pre-fix code** with normal teleport forced on —
confirming it is a pre-existing, deterministic, boundary-timing
characteristic of this specific edge/window/demand combination (the
vehicle's free-flow-estimated arrival sits within roughly a minute of the
window's own end), independent of this fix, and correctly caught rather than
silently published. This was a direct CLI verification exercising the exact
production routing code path (`reroute_closure_affected_vehicles` /
`closure_routing.py`); it is NOT a literal replay of the frozen
`daily-unit-24737391111be0e137537df7` monthly-search harness (constructing
that harness invocation directly was not completed in this session — see
TASKS.md's current block for the precise remaining gap). Do not read the
9-10 s figure as a monthly-campaign-wide measurement; it is one closure,
one seed, one demand day.

Held-out closure selection also requires the edge to survive its own closure.
SUMO internal junction edges are collapsed only for this topology probe, which
searches from the real edges immediately before the closure to its immediate
successors after removal. Demand reachability continues on SUMO's raw graph.
Denied departures are reported access impact and do not gate. Monthly result
identity fingerprints the teleport module; held-out v10 fingerprints
`run_scenario.py` and both closure modules. Monthly backend provenance
(`monthly_sumo.py`'s 19-file `source_digest`/`simulation_source_digest`) now
also hashes `closure_routing.py` and `disruption.py` (21 files), so no cache
or warm state produced under the retired policy can satisfy a lookup under
this one.

Held-out closure selection also requires the edge to survive its own closure.
SUMO internal junction edges are collapsed only for this topology probe, which
searches from the real edges immediately before the closure to its immediate
successors after removal. Demand reachability continues on SUMO's raw graph.
Denied departures are reported access impact and do not gate. Monthly result
identity fingerprints the teleport module; held-out v10 fingerprints
`run_scenario.py` and both closure modules.

### LOSO integer-publication boundary

Production update 2026-08-09: protocol
`loso_pfe_meso_v11_observability_gate` supersedes v7-v10 for new evidence. The
canonical publisher in `traffic_sim/demand/pfe.py` now solves one joint integer
projection over every pool-supported active sensor, every retained hard bound
and an exact continuously achieved purpose margin. Sensor keys are sorted, so
registry insertion order cannot become a priority. It tries exact rounded
sensor margins first, preserves the continuous interval total only when
compatible, and enters only the continuous solver rung's declared measurement
band after exact infeasibility. Unsupported sensor edges remain explicit pool
coverage defects in the report instead of impossible empty equalities. Any
remaining hard infeasibility closes the publication gate before a temporary
route file is opened. The old validation-only v8 implementation remains for
historical decomposition, not as a second production path.

Adding a sensor therefore needs no rounding code change: its resolved edges
enter the incidence constraints automatically. Integer publication also keeps
every inactive registered sensor's rounded continuous margin as a lower-priority
shadow constraint, without reading its held observation. This prevents purpose,
structure and provenance repairs from arbitrarily moving an otherwise valid
continuous held prediction.

The capacity contract is 50 physical sensor stations. Sensor count expands
calibration constraints and the bounded final audit. It can also indirectly
increase the calibrated population when newly observed movements require more
route instances; that vehicle growth is part of the supported product. A route
may satisfy multiple sensor margins, so station counts must be solved jointly
and must never be added as 50 independent city populations. Interactive closure
p95 remains <=10 s at 50 stations only for the fixed-load reference arm; actual
50-station releases additionally pass calibrated 21k/32k/43k/50k/60k load
tiers and fail closed on uninserted or waiting vehicles. Demand-build,
sensor-only and vehicle/network-load scaling are measured separately. The test
matrix and service budgets are in
`docs/plans/FIFTY_SENSOR_PERFORMANCE_CONTRACT_2026-08-22.md`.

The vehicle-level sensor-anchor contract is checked again on the final route
instances, after integer projection and purpose-route replacement. Every real
demand build and LOSO fold passes the union of the current registry's resolved
sensor edges to the publisher. If one final vehicle crosses none of them, the
staging XML is deleted and no route file is published. The report records the
resolved registry edges, the unanchored vehicle count and a pass/fail result.
This is deliberately based on the full physical registry in LOSO: holding out
a sensor hides its count, not the street or the traffic that crosses it.

Two generic onboarding gates cover what rounding cannot solve. First,
`build_candidates.py` requires every measured edge to retain at least
`--min-per-sensor` **distinct physical route geometries** in the final routed
pool; repeated vehicles and reused day-template copies cannot make a thin sensor
pass. Every final candidate also has to cross at least one registered sensor
edge before calibration starts. Second, every LOSO station receives an
incidence-rank certificate against active sensor margins plus interval total. A
rank gain means the held total is underidentified: the ratio remains useful
diagnosis but cannot certify a new sensor contribution.
`traffic_sim/intake/contribution.py` therefore refuses improvement claims under
the v11 contract unless `onboarding_ready=true`. This does not invent
information: it makes the irreducible limitation explicit and fail-closed.

Pool/picker robustness update v13 closes the remaining pre-warming identity and
support gaps generically. `data_in/sensors.json` and `web/data/flows.json` must
name exactly the same non-empty reviewed edge set; both the registry and its
loader are part of the candidate-cache key. Edges are sorted before quota and
bit-mask construction, so registry/JSON insertion order cannot change the pool.
The cache also fingerprints the exact `duarouter` executable bytes. A SUMO
upgrade therefore cannot restore geometry produced by an older router and then
publish it under the new runtime identity. This is material because SUMO's
[routing changelog](https://sumo.dlr.de/docs/ChangeLog.html) records behavioural
changes to randomised edge weights, while its
[`duarouter` contract](https://sumo.dlr.de/docs/duarouter.html) defines both the
`--weights.random-factor` semantics and the automatically generated alternative-
route sidecar.
The cache identity also records Python/platform and the NumPy, NetworkX, OSMnx
and Shapely versions used by candidate generation. NumPy's
[`Generator` contract](https://numpy.org/doc/stable/reference/random/generator.html)
does not guarantee identical streams across versions, so source hashes and a
fixed seed alone are not a complete pool identity. The calibrated-day library
likewise binds Python/platform plus NumPy/SciPy/Numba and the complete canonical
demand-source inventory. A newly added helper such as
`traffic_sim/demand/structure_caps.py` can therefore no longer change picker
semantics while matching an older stored day.
Reusable route geometry is drawn from a canonical weekday/weekend departure
profile, while each calendar day's measured or forecast profile controls only
departures; the first date encountered in a multi-day window can no longer
alter the template. These contracts fail closed automatically when a sensor is
added, removed or re-snapped.

The `build_sumo_demand.py --candidate-source catalog` path materializes
that boundary as a content-addressed `sumo/route_catalog/` artifact. A bounded
per-key file lock prevents duplicate producers; manifests verify every XML,
metadata and validation byte before restore; and mixed weekday/weekend windows
merge prefixed candidate IDs without changing the physical route geometry.
The catalog removes only date-shaped inputs from the identity and retains the
network, sensor edge set, route gates, runtime and complete demand-source
inventory. The daily purpose-by-quarter margin is passed explicitly to PFE.
The two-date invariance and current seven-edge sensor-coverage gates passed on
2026-08-24. The first unequal 12,000/6,000-candidate campaign is retained only
as diagnostic history. Its schema-v2 replacement ran 30 counterbalanced pairs
with 6,000 candidates in both arms and passed every declared gate: median wall
time 55.246→24.715 s (2.235x ratio of arm medians; 2.220x median paired
speedup), catalog adapter p95 0.678 s, no slower day class,
paired vehicle-population deviation at most 0.761% and peak RSS below 8 GiB.
PFE time is an end-to-end product measure, not an isolated solver comparison:
the old campaign did not record the distinct route×purpose variable count, so
it cannot prove equal PFE workload. New campaigns record that count explicitly.
Suite/negative-path contracts are bound once per campaign, while seven
build-specific contracts are measured independently in each arm. A schema-v3
`sumo/route_catalog_adoption.json` makes catalog the normal default only after
it opens and hashes the named qualification and build reports, follows and
hashes their trials and suite-evidence links, cross-checks exact catalog keys
and selected sizes through the chain, and verifies the immutable entries.
Missing, stale, legacy-schema, corrupt, path-escaping or unbound records fail
safely to legacy. A source/input mismatch discovered after argument parsing
also falls back to the qualified legacy candidate builder when catalog use was
implicit; `demand_meta.json` records the catalog-to-legacy reason and selected
pool. An explicit `--candidate-source catalog` remains strict and may build
only an isolated, unadopted catalog. `tools/build_route_catalog.py` owns
isolated materialization and the bounded
sensor-support sizing ladder; `tools/verify_route_catalog_invariance.py` owns
the two-date gate; `tools/qualify_route_catalog.py` applies the frozen 30-pair
performance contract, including equal candidate requests in both arms. Only
`tools/adopt_route_catalog.py` may write the small adoption record that changes
the default, and it refuses evidence that is not cryptographically bound to
the supplied build and stored catalog bytes.
An explicit `--candidate-source legacy` always remains the rollback path.
The old unmatched campaign measured 66.402 s versus 19.437 s and is useful for
diagnosis only. The matched campaign is bound in
`validation/route_catalog_qualification_v3_2026-08-24.json`; seven catalog
fixtures plus explicit legacy rollback passed the versioned soak report, so
the catalog is now the production default.
Catalog reuse is still bypassed explicitly
for `--congestion-iterations > 1`, because feedback weights make route supply
date-specific. Annual warming is not implied by adoption: it remains keyed to
the exact finished daily route bytes. The refreshed preflight and one q50 unit
passed; the other 104,684 planned units remain unexecuted.
Mixed weekday/weekend materialization prefixes both vehicle IDs and embedded
`tour_id` values, so tour provenance cannot cross day-type namespaces. PFE may
use wider continuous feasibility rungs internally, but the publication worker
retries at the exact no-bounds rung and fails closed unless every rounded
sensor-edge/quarter target is met exactly.

Randomised duarouter costs can produce a loop or excessive detour from an
otherwise grounded OD/via request. After the ordinary physical filters, v13
reroutes only the missing original requests once with random factor 1.0 and
applies the same endpoint, via, SUMO-edge, U-turn, global-detour and local-
roundabout gates. Only surviving routes are merged; no traffic is invented and
no realism gate is relaxed. The transient `.alt.xml` route-distribution files
are deleted because no downstream stage consumes them. On the picker side,
path-size overlap counts a physical route geometry once across all purpose
variants, preventing provenance expansion from silently changing the route-
choice prior.

The targeted v11 107 screen preserved both component edges within zero vehicles
per quarter of their rounded continuous shadow margins and produced ratio 1.615
(GEH<5 29.2%). The v13 production-pool screen produced 1.644 at the same station,
so v13 is not claimed as an absolute LOSO improvement. Station 134 moved from
ratio 0.819/GEH<5 87.5% on the old pool with the current picker to
0.844/91.7% on the v13 seed-20260811 pool: better hourly fit but mixed daily
ratio. All six current stations remain structurally underidentified when
individually held out (rank gain 1), so more runs alone cannot remove the
missing information.

The production pool result is nevertheless materially safer. Two real 12,000-
request seeds retained 9,280 and 9,309 ordinary candidates after every gate,
with 5,964/5,998 distinct routes, 3,619/3,691 OD pairs, 3,729/3,769 covered
network edges, zero unanchored routes and a minimum of 516/527 distinct routes
per sensor against the floor of 50. Deterministic recovery raised the first
seed's final supply margin from 75.35% to 77.33% and added 153 distinct routes,
135 OD pairs and 14 network edges. Lowering route diversity from 2.0 to 1.5 was
rejected because it cut distinct routes to 4,935 and minimum sensor support to
390 despite retaining more vehicles. Compact evidence is frozen in
`validation/pool_picker_robustness_v13_20260809.json`.

An explicit active-only anchor experiment was rejected: removing routes that
cross only held station 107 reduced its recovery ratio to about 0.18. Those are
real, sensor-observed movements whose observation is hidden only for scoring;
deleting them changes the physical demand problem. A two-seed candidate-pool
union was also rejected as a generic pool improvement: 107 worsened from 1.615
to about 1.67 while 134 improved to about 1.03, demonstrating mixed allocation
and volume inflation rather than a transferable correction.

Because the sensor registry, `build_candidates.py` and
`traffic_sim/demand/pfe.py` are production-bound demand inputs, any new sensor
or implementation change invalidates the former annual warming plan/bank. The
current replacement plan key is
`66fb46d46e751b86bb1851be148e17a6d921288396b97868d0b28c73a4ee6177`;
preflight `6f2d99700e06…` passes, but population is deliberately not started
while absolute held-out quality remains rejected.

LOSO fold solving and route publication are separate correctness boundaries.
The continuous solver may retain assignment-field ceilings that model a held
station as unmeasured, but measurement-preserving integer rounding and
best-effort structure repair can move mass among routes with identical active
training-sensor incidence and different held-edge incidence. Protocol
`loso_pfe_meso_v7` therefore passes only the held-derived assignment ceilings
to `write_calibration_report` as enforced integer bounds. Other wide assignment
bounds remain continuous diagnostics, preserving the historical fold model.
Retained-rung integer infeasibility fails closed and no partial fold route file
is published. This is validation-only behavior in
`traffic_sim/confidence/loso.py`; production PFE behavior is unchanged.

Opt-in decomposition lives in
`traffic_sim/confidence/picker_diagnostics.py`. It records the completed
continuous solution without changing `pfe.py`, then
`tools/analyze_loso_picker_diagnostics.py` reconstructs direct rounding and
compares it with the exact published route and SUMO edge artifacts. Diagnostic
replay is not release evidence and held counts never select constraints or
treatments.

Protocol `loso_pfe_meso_v8_controlled_rounding` is an opt-in validation
treatment, not the production publisher. Its implementation lives in
`traffic_sim/confidence/controlled_rounding.py`. For each quarter it jointly
projects the continuous route×purpose vector to nonnegative integers while
preserving active rounded sensor margins and the rounded interval total, then
feeds that warm start through the unchanged purpose, structure and held-bound
repair path. The held station is absent from the projection. LOSO temporarily
substitutes this initial rounder only after its continuous worker pool closes,
precomputes all publication counts, and restores the production helper in a
`finally` block. `traffic_sim/demand/pfe.py` remains byte-unchanged.

Exact integer margins are not always mutually feasible: station 107 exposes at
least one such quarter. The validation module constrains any exact-infeasible
solution to the continuous solver rung's already declared measurement band and
minimises maximum residual, residual sum and route L1 lexicographically. Paired
conflict analysis finds exactly quarter 11 in both station-107 seeds; its
incoming target 4 conflicts with the two pool-supported outgoing targets 2+1,
while the interval total is not part of the irreducible set. The band treatment
is technically valid but quality-rejected (`1.333/2.001` versus v7
`1.220/2.005`). Stations 134/2276 have exact feasible margins in the paired v8
screen.

### Demand build identity

`DemandBuildSpec` is the boundary contract for recalibration. It carries the
start date, historical/forecast source, consecutive-day range, effective
window, and fixed structural-reference date. The API archives it before the
background job; `build_sumo_demand.py` validates any legacy flags against the
same object and writes the canonical copy to `sumo/demand_build_spec.json` only
after calibration succeeds. Its content key and all demand-affecting solver
options participate in the demand fingerprint. A scenario may be published
only when its `build_id` and `demand_build_key` match `demand_meta.json`; a
failed replacement therefore leaves the previous demand/scenario release
coherent and serving.

Golden releases extend that same registry under `runs/releases/`; they copy
only explicitly named case-artifact bundles and activate through one atomic
pointer. A normal/closure bundle keeps its scenario, representative trajectory
and exact route inputs under one integrity gate; per-case subdirectories retain
same-named producer manifests without collisions. It must not freeze a scenario
JSON while leaving referenced dependencies mutable. Neither demand nor scenario
runs may discover release inputs by globbing the shared `sumo/` or
`web/data/scenarios/` directories.
Golden activation additionally requires passing case records plus explicit
full-suite, browser/API, peak-memory and rollback gates; a staged release with a
pending gate is not valid merely because its copied files are intact.
The golden rollback path applies those same checks to the predecessor before
flipping the pointer, so a formerly valid but later damaged release cannot be
restored.

The domain packages `demand/` and `dirsplit/` remain separate because they are
model-specific pipelines with their own data contracts. `web/` is the browser
runtime, `tools/` contains bounded experiments, `tests/` contains contract and
regression tests, and generated artifacts stay under `web/data/`, `sumo/`,
`runs/` or `cache/` rather than in source packages.

The browser keeps measured input and model inference distinct. Sensor
deliveries contain station, date, 15-minute interval and passage count; they do
not contain observed trip purpose. The `arbete`/`service`/`fritid` values in
`agent_demand` are therefore labelled as modelled trip purposes, while
`external` and `through` are shown separately as geographic route categories.
Internal representative seed and demand-variant identifiers remain in scenario
artifacts and validation evidence but are not exposed in the normal vehicle or
sensor-audit labels.

### A — Intake (`build_data.py`) — built
Validate 15-min CSVs; join coordinates + **measured-direction metadata**
(`data_in/sensors.json` — the delivered "Total" label is proven unreliable);
direction-aware snapping (bearing must match; true point-to-polyline
distances). Gate: every station snapped ≤ 60 m with matching bearing.

### B — Mathematics (`observability.py`) — BUILT
From graph + measured edge set:
1. Junction solves: where all-but-one leg of a junction is constrained,
   derive the remainder exactly (the Läraregatan/Gibraltargatan case).
2. Interval bounds: per unmeasured edge, min/max flow LPs subject to
   conservation (with small slack for driveways/parking), measured values,
   non-negativity and a lane-capacity cap. (Scandinavium-side case: too
   open for exact solves, still yields bounds.)
3. Corridor consistency alarms: residuals between stations sharing a street
   (107 vs 1076 on Skånegatan) — a data-quality gate, run at intake time.
Output: `observability.json` (exact values, intervals, alarms, classes).

### C — Destinations (PFE-lite, evolves `build_sumo_demand.py`)
1. Candidate routes: randomTrips pool, **weighted by the prior** —
   gravity-style OD plausibility (SCB population/workplaces) and the
   learned street/time profiles (dirsplit model, in its correct role).
2. Solve the PFE LP (scipy): route-use variables ≥ 0; hard equalities at
   measured edges (per 15 min, per direction where known); inequalities
   from B; L1 deviation terms pulling edge flows toward level-3 priors with
   weights ∝ 1/uncertainty; total-count regularisation.
3. Emit routes (q50 + uncertainty variants) + the implied OD matrix.
routeSampler is kept as reference implementation/fallback (it cannot
express per-edge weights or intervals — verified against its docs).
BUILT (pfe.py + prior_flows.py + --engine pfe): unserveable counts are
dropped per-constraint (never fatal to an interval) and a relaxation
ladder (tol ×2, ×4, then without level-2 bounds) guarantees non-empty
intervals. Gate: GEH < 5 at ≥ 85 % of measured hourly values — the FHWA
calibration criterion. Current whole-day: 92–93 % GEH, delivery at
measured edges mean 0.88, opposite-direction priors delivered 0.64–1.01.

**Production integer structure contract (2026-08-09).** The continuous PFE
solution and final integer route counts are separate boundaries. Optional
origin/short-trip concentration groups are repaired jointly with retained
sensor margins, hard bounds and purpose groups. Once a group overflows during
one quarter's repair it remains in a cumulative active set for every later pass;
otherwise fixing group B can silently re-break group A. The loop is finite:
each useful pass activates a previously inactive group and the number of groups
is fixed. Audit and solver both call
`traffic_sim/demand/structure_caps.integer_structure_cap`, so a fractional
share limit cannot be interpreted differently after integer publication. The
active build `dbb44172f30778adf8c0` verifies zero under-1-km cap violations and
no structure flags without changing sensor targets or accepting held counts.

### C — Candidate generation (`build_candidates.py`) — GROUNDED (2026-07-05)
The route-candidate pool (what PFE selects among) is now the standard
**subarea/cordon** structure (FHWA/state-DOT subarea-analysis practice;
Cascetta's quasi-dynamic OD) with REAL data at every endpoint, replacing
uniform `randomTrips`:

**Every-edge support correction (2026-08-04).** Behavioral candidates remain
sensor-conditioned, but the published pool also contains one provenance-
labelled exact shortest legal SUMO-connection route for every otherwise
unsupported drivable edge. U-turn connections are excluded unless an isolated
component has no other legal route, in which case the exception is explicit.
After PFE, a deterministic greedy set cover adds the minimum support-only
vehicles needed for each calibrated variant without crossing measured edges.
They participate in SUMO and closure effects but not behavioral purpose,
length or OD statistics. Publication requires 7,125/7,125 candidate and
q10/q50/q90 edge equality plus exact candidate-route/agent provenance. The PFE
touch index is constructed once before worker fork and reused without changing
numeric order or output.

**Exact sensor-count publication gate (2026-08-23).** A directional forecast
target can be fractional, but a route file can contain only whole vehicles.
For every registered directed sensor edge and every 15-minute interval, PFE
must therefore publish exactly `int(round(target))` route crossings. The fit
report records the number of constraints, exact matches, maximum and summed
absolute integer residual; production publication refuses a missing record or
any non-zero residual. GEH remains a secondary conventional diagnostic and
cannot make a non-exact build publishable. SUMO's `loaded`/`inserted` counters
are a separate runtime integrity check: they prove that the simulator accepted
the already calibrated route file, not that the simulation added demand.

**Strict fastest sensor-route basis (2026-09-01; supersedes the 2026-08-26
forced-via search).** Candidate routing may propose diverse sensor-conditioned
ODs, but the PFE publication boundary admits a geometry only after replacing it
with the deterministic global fastest passenger route for the same OD. The
declared sensor must already lie on that route. Removing each measured sensor
crossed by the route must leave a finite legal route whose free-flow cost is
strictly higher (absolute tolerance 1e-6 s, relative tolerance 1e-9). The graph,
first-lane edge costs, tie-breaking and endpoint convention are shared with the
deterministic closure scorer. Every accepted route receives a network- and
geometry-bound `sensor_shortest_positive_gap_v1` proof; PFE, catalog validation
and catalog combination fail closed on a missing or incompatible proof.

The grounded support search now selects OD pairs directly from unrestricted
global shortest-path trees; it never constructs a forced-via path in a graph
with the other sensors removed and then compares that path with a different
global optimum. Both existing tour directions are eligible: anonymous
home->activity and the grounded return activity->home. For every sensor the
generator fills the configured distinct-route floor with routes whose measured
incidence is exactly that sensor, using only existing home/activity endpoint
pools. Every support OD must independently satisfy the finite strictly-slower
sensor-avoiding test. This exclusive non-negative basis is sufficient for every
rounded non-negative sensor target vector. If the floor cannot be met, candidate
generation fails before PFE and names the unsupported edge; it never restores
bounded detours, equal-cost alternatives, unreachable reroutes or synthetic
background traffic. The older every-unmeasured-edge mechanism remains
historical and disabled.

The configured floor is 50 distinct OD geometries per directed sensor edge.
It is an evidence-selected operating point, not a universal choice-set size.
A preregistered same-protocol 2025-09-16 LOSO ablation tested floors
25/50/100/200/500. Their median multiplicative daily errors were respectively
1.642x/1.560x/1.946x/2.293x/2.425x and mean hourly GEH<5 was
55.8%/57.3%/49.6%/43.3%/37.0%, so 50 won both the primary and secondary rule.
Every pool passed fastest/positive-gap qualification and exact active counts,
but every held station remained underidentified. Larger floors add
sensor-exclusive route variables without adding independent measurements;
candidate count alone is therefore not a proxy for observability or held-out
accuracy. The explicit diagnostic floor is part of candidate-cache and demand
provenance, and changes require a frozen same-protocol ablation.

**Exact simulated-passage diagnostic (2026-08-23).** A baseline scenario now
also retains raw 15-minute `edgeData` counts for every directed sensor edge,
the unrounded three-seed ensemble mean, the animated representative seed and
every individual seed. `raw_edgedata_15min_exact_integer_targets_v1`
recomputes all evidence from those detailed arrays and compares each cell with
the same declared integer target. A lying compact summary, a missing seed row
or an ensemble average that hides opposite per-seed errors is rejected. This
test is diagnostic and non-mutating: it does not add vehicles, move departures
or change routes, and road-closure scenarios deliberately do not claim
baseline exactness. The validation UI exposes it as a separate gate rather
than replacing the existing hourly GEH publication check.

On the active 2027-09-08 forecast baseline, the demand route file is exactly
672/672, but raw SUMO passage time is only 81/672 exact in the ensemble
(12.053571%; maximum absolute residual 16.333333 and summed residual 1910).
The representative seed is 114/672 exact and the three seeds are respectively
114, 119 and 120 exact. This establishes the next correctness task: reconcile
departures with simulated sensor crossing time while freezing population,
OD, purpose and route provenance. Until that task passes, the exact-passage
section correctly remains `warn`.

**Departure-reconciliation diagnostic (2026-08-23, rejected).** SUMO's
documented clocks explain the mismatch: route `depart` is the requested network
entry, `tripinfo.depart` is the real insertion time, and edgeData `entered`
counts the later upstream-to-sensor-edge transition. The default passenger-car
speed deviation is also sampled during vehicle loading, so changing XML order
changes which id receives which driver speed. The isolated
`tools/departure_reconciliation.py` prototype therefore intersected
the observed passage-safe departure windows for seeds 1000/1001/1002 and kept
the original vehicle order. It changed no route and added/removed no vehicle;
all three runs reached 672/672 exact with clean health and retained three
distinct speed profiles.

That numerical success is deliberately **not production**. Order preservation
compressed the active route from a 2.7 s median departure gap and at most one
departure per second to a 0.1 s median, 15,545 minimum-gap adjacencies and up
to ten departures per second. Five counterbalanced baseline trials also put
the candidate median 3.15% slower. Manufacturing insertion convoys is not a
valid city-demand calibration. The module now rejects that shape before
publication, as well as incomplete evidence, population/route drift, collapsed
seed profiles, unhealthy SUMO output or any non-exact sensor cell. The active
route remains unchanged and the raw passage section remains `warn`; the bound
diagnostic is recorded in
`validation/departure_reconciliation_diagnostic_2026-08-23.json`.

SUMO's calibrator is not an acceptable shortcut for this contract because its
documented behavior inserts vehicles when flow is low and removes vehicles
when flow is high or jammed.

`tools/standard_driver_pool.py` now implements the safer design as an isolated
post-picker layer. The picker still chooses a date's exact vehicles and routes;
the pool identity binds that date, demand build, route bytes, network, sensor
targets and three profile arms. Each arm assigns an explicit deterministic
per-vehicle `speedFactor`, so XML ordering cannot silently remap random driver
attributes. All arms share one passage-aware departure schedule. The scheduler
first tries to reuse the complete original slot set and otherwise uses an
earliest-deadline feasible ordering projected toward those slots, guarded by
the same anti-convoy checks. Baseline and closure for one arm must later reuse
that arm unchanged (common random numbers).

The real 2027-09-08 diagnostic kept all 21,240 vehicles and every route, kept
three distinct near-default speed distributions, cleanly inserted every
vehicle, and reached raw 672/672 in every arm after two offline iterations.
Departure spacing remained non-bunched (minimum 2.0 s, median 2.7→2.5 s,
maximum one departure/s), although 21,183 vehicle ids exchanged latent origin
times with a median absolute shift of 254.4 s. It is still **not production**:
ten counterbalanced three-arm baseline timings measured a 1.805 s current
median versus 1.846 s for the pool (+2.30%), and no production-shaped closure
comparison exists. The tool writes only to a caller-selected content-addressed
experiment directory and cannot replace `sumo/calibrated.rou.xml`; therefore
it has not invalidated the current warming. See
`validation/standard_driver_pool_diagnostic_2026-08-23.json`.
  - **E-E** (through) — gate→gate, gate weight ∝ approach-road class
    (motorway/trunk draw more than a residential fringe street — the only
    local proxy available; no external cordon counts exist to calibrate
    gate weights better — disclosed limitation).
  - **E-I / I-E / I-I** — PAIRED candidate-tour support (the return leg is
    generated from the same tour's second half, not a fresh sample, providing
    an AM/PM directional prior). PFE then calibrates aggregate 15-minute
    trip uses independently, so the final vehicle animation does not claim
    persistent individual return journeys. CORRECTED 2026-07-09: this line described E-I/I-E
    as already working from this doc's original 2026-07-05 lock — false;
    only I-I (both ends internal) was ever implemented until 2026-07-08/09,
    which structurally capped tour length at the canvas's own diameter (see
    the θ entry below). E-I/I-E (one end a boundary gate) are now real,
    added via `--cross-fraction` (disclosed neutral prior, same status as
    through_fraction).
    HALF TOURS (2026-08-06): the pairing above holds in the GENERATOR, but
    `validate_routed_candidates`, `drop_uturn_routes` and
    `drop_excessive_detours` each delete individual legs and none knows a
    tour has two. Measured: 1,316 of 2,695 non-through tours (48.8%) reached
    the delivered pool with one leg, and the loss is directional — a return
    leg must reach an independently drawn gate AND pass a sensor, so it
    routes more circuitously and is filtered ~1.8x more often than an
    inbound one. The surviving leg is KEPT (the pool is a coverage support
    set; the PFE reweights freely and never reads the pairing, and dropping
    costs 13.9% of the pool, below the 75% supply floor) but its
    `candidates.meta.json` record now carries `tour_partner_dropped: true`,
    which flows into `calibrated.agents.json` so no consumer can mistake a
    half tour for a tour. Every build prints the count and the per-leg
    split. `--atomic-tours` drops instead, for work that does consume
    pairing. The composition bias is measured and visible, NOT corrected —
    correcting it means generating replacement tours.
    Purpose is sampled from RVU's split (43/33/24 %, its WEEKLY average —
    Fig.11 has no day-type qualifier), but NOT as one flat number: split
    into weekday/weekend/holiday profiles AND by hour of departure (e.g.
    08h is ~81 % arbete on a weekday, 20h ~35 % fritid) — triangulated from
    RVU's total plus two external sources with real weekday/weekend/hourly
    granularity (NHTS 2017, UK NTS 2019), solved so the annual average
    reproduces RVU's 43/33/24 exactly (`build_candidates.PURPOSE_HOURLY_
    WEEKDAY/WEEKEND`). Destination drawn from that purpose's activity mass
    with a gravity deterrence.
  - **HOME mass** = real 2023 population per DeSO zone (`fetch_deso.py`:
    SCB open WFS for DeSO-2025 boundaries + PXWeb API for population,
    both live, no key needed), spatialised to anonymous residential-building
    footprints and their nearest usable road access. Building footprint area
    and levels are only a within-zone capacity proxy; the SCB zone total stays
    fixed for every DeSO with routable access. An official
    `data_in/deso/buildings.geojson` takes precedence (WGS84/EPSG:4326
    GeoJSON, longitude/latitude order); cached OSM footprints are the open
    fallback. A DeSO with no edge in the inner-city graph is
    explicitly reported and is not fabricated as an interior home. 116 of
    129 inner-city DeSO zones have data (13 are brand-new 2025-boundary splits
    not yet back-filled by SCB — disclosed, not silently dropped). Primary
    sources: [SCB DeSO open geodata](https://www.scb.se/vara-tjanster/oppna-data/oppna-geodata/demografiska-statistikomraden-deso/)
    and [Lantmäteriet Byggnad](https://www.lantmateriet.se/sv/nationella-geodataplattformen/datamangder/byggnad/),
    whose schema supplies building geometry, purpose and area when a complete
    official delivery is available.
  - **ACTIVITY mass** = OSM POIs in 3 categories matching RVU's purpose
    categories (arbete/service/fritid), each mapped once to its nearest
    usable road access rather than diffused across a broad road halo.
    DOCUMENTED PROXY: true workplace-
    location microdata (RAMS) was checked and confirmed NOT free below
    kommun level (SCB's day-population-by-workplace tables stop at 312
    kommun codes) — POI density is the honest substitute, same spirit as
    the road-attribute proxies used elsewhere in this project.
  - **Departure times** = our OWN measured `normal_profile.json` shape
    (finer-grained than RVU's coarse bins, and independently consistent
    with RVU's reported AM 7-8h / PM 16-17h peaks — a genuine cross-check,
    not a coincidence, since both measure the same city).
  - **θ (through_fraction, gravity_km, cross_fraction)**: a 3×3 bounded grid
    search (`calibrate_theta.py`) — the proportionate version of
    simulation-based calibration (SPSA/metamodel methods, see references)
    for a small number of free parameters. FINDING: GEH-based scoring on a
    fast morning window SATURATED at 100 % for all 9 combinations — it only
    checks fit at MEASURED edges, which the PFE achieves regardless of
    candidate composition given enough route diversity; it cannot
    discriminate θ. CORRECTED 2026-07-09: this doc (and calibrate_theta.py's
    own commit message) had claimed since 2026-07-05 that GEH-scoring was
    "replaced with a trip-length fit against RVU's measured distance bins"
    — that fit was never actually implemented until 2026-07-08; re-running
    it for real gave gravity_km=2.6 as the best of the original 3×3 grid
    (L1=0.5947 against RVU's short bins), though a wider manual sweep found
    gravity_km=12 fits marginally better (L1=0.5812) — an interior optimum,
    not monotonic. HARD CEILING, verified by direct measurement (not the
    "2 km-wide canvas" this doc previously and incorrectly claimed): this
    network's own diameter — gate-to-gate (E-E) AND gate-to-interior (E-I/
    I-E) alike — never exceeds ~7.8 km. RVU's 5.1-10km/>10km bins (51 % of
    all real trips) describe a WHOLE-REGION survey including long regional
    commutes; most of a trip like that happens on roads outside this graph
    entirely, so no θ value can close this gap — it is a scope mismatch
    (distance-within-this-graph vs. real door-to-door distance), not a
    mistuned parameter. E-I/I-E tours (added 2026-07-08/09, see above) are
    a genuine, if modest, improvement on this front (5.1-10km share:
    ~1-4 % I-I-only → 8.4 % E-I/I-E-only) since a gate-anchored end can span
    the full canvas diameter, unlike a purely internal I-I tour.
    through_fraction and cross_fraction have NO local ground truth to
    discriminate them against — frozen at 0.5 and 0.3, disclosed as
    unidentifiable neutral priors, not calibrated values.
  - **Validation — controlled A/B via `--legacy-random-pool`**: median LOSO
    recovery on the SAME city-scale network, same corridor coupling, same
    shape pool: grounded 0.093 vs legacy uniform pool 0.076 — a modest
    (+22 % relative) but genuine improvement, dominated by one edge (107's
    toward-centre direction, 0.95 vs 0.06) with a mixed, noisy picture
    elsewhere (n=7 measured directed edges). NOT a leakage artifact:
    verified `validate_sim.py` does not consume `corridor_priors` at all
    (a real gap vs the deployed pipeline, noted below).
    CONFOUND WARNING for anyone reading project history: an EARLIER LOSO
    number (0.32 median) was measured on the small two-cluster network,
    before the city-scale expansion, corridor coupling, and the PFE
    shape-pool fix — it is NOT comparable to the 0.09/0.093 figures above,
    which are both on today's full inner-city network. Recovery is
    structurally harder at city scale (vastly more unconstrained
    alternative paths) — lower numbers here are not a regression.
    STALE AS OF 2026-07-09 — PENDING RE-VALIDATION: every LOSO figure on
    this page (0.093/0.076 here, 0.09/0.154 in C.1, 0.32 in section F) was
    measured BEFORE E-I/I-E tours and hour/day-type-aware purpose sampling
    existed — the candidate-generation mechanism these numbers describe
    has materially changed since. Not yet re-run (a real compute cost, a
    deliberate choice to defer rather than guess at updated figures) — do
    not treat these as the current system's validated behaviour until
    `validate_sim.py` is re-run against the current pipeline.
  - FIXED 2026-07-09: `validate_sim.py` now wires in `corridor_priors`
    (`corridor_priors_for_fold()`, excluded per-fold whenever either anchor
    sensor is the one being held out — same leakage-prevention principle
    as `prior_flows.json`'s direction priors). The underlying mechanism was
    already fully general (scans every PAIR of measured sensors, no
    hardcoded IDs — new stations get corridor priors automatically), so
    this was a validation-ACCURACY gap only, not a scalability one; the
    real, deployed pipeline already had this. The LOSO figures above still
    predate the fix (see PENDING RE-VALIDATION above) — re-running will
    now also reflect corridor coupling, closing this specific
    underestimate alongside the E-I/I-E and hour/day-type-purpose changes.

### C.1 — Why grounding barely helped, and the fix that did (`assignment_priors.py`)
Gustav asked directly: why did realistic OD grounding only move LOSO from
0.076 to 0.093? Root-caused, not guessed: **`pfe.py`'s objective minimises
Σ x_r · EPS_PARSIMONY (total vehicle count) with NO offsetting pull on any
edge that carries no active hard constraint or soft prior.** A route only
gets LP weight if it serves an active constraint. `build_candidates.py`'s
population/POI grounding shapes WHICH routes exist in the candidate pool,
but does nothing to make the PFE actually USE routes through an edge unless
that edge (or one on the same route) is itself constrained. Confirmed
directly: station 107 recovered brilliantly in LOSO (0.95) because it sits
on paths between OTHER active constraints (the corridor-coupled 1076);
isolated stations (1074, 1076, 134) collapsed toward zero because nothing
pulled weight onto routes through them once their own count was hidden.
This is the classic 4-step transport model's missing 4th step — **traffic
assignment** — and the standard fix (Dial's stochastic multipath loading,
gravity-distributed) is what `assignment_priors.py` implements: gravity-
distribute the SAME home/activity masses across many sampled OD pairs
(now including E-E gate pairs, not just tours), route each via shortest
path, accumulate a loaded-flow field that is non-zero on every reachable
edge, and feed it in as a weak, wide-margin signal for edges no stronger
source covers.

THREE REAL BUGS FOUND AND FIXED DURING DEVELOPMENT (each verified with a
before/after diagnostic, not just reasoned about):
  1. **Length vs. time weighting.** First cut used physical edge length for
     shortest-path routing. Real route choice minimises travel TIME —
     length-weighting sent the assignment down slow residential shortcuts
     instead of the arterials sensors actually sit on.
  2. **All-or-nothing vs. stochastic multipath.** Even with time-weighting,
     a single deterministic shortest path per OD pair put ZERO load on 6 of
     7 sensor edges (verified directly) — all traffic collapses onto one
     canonical route, missing "good but not literally fastest" arterials.
     Fixed with Dial's classic mitigation: route each sample through one of
     several (10) randomly-perturbed (lognormal-jittered) travel-time
     graphs, spreading load across realistic alternatives.
  3. **LP intractability.** Feeding the assignment field in as ~6 500 soft
     L1 priors (pfe.py's `priors_pq`) — each costing 2 new LP variables + a
     row — stalled a whole-day solve at 100% CPU for 35+ minutes with zero
     output (killed, confirmed not simply slow). Fixed by feeding it in as
     a **wide interval BOUND** instead (`bounds_pq`, the same free,
     variable-less mechanism level-2 conservation bounds already use:
     [0, 5×assignment-value]) — arguably more honest too, since this field
     is a rough plausibility range, not a confident target. Whole-day solve
     time: 35+ min (stalled) → 7 min.

CALIBRATION, NOT TRANSFER (avoids the volume_priors.py mistake): the scale
converting loading-units to vehicles/day is fit LOCALLY by ROBUST median
ratio (not least-squares — see code comment on why LS with n=7 was unstable)
against our own 6-7 measured edges. No cross-city ML transfer risk.

**FINAL LOSO VERDICT — replicated twice, same direction both times:**
| | median LOSO recovery |
|---|---|
| Grounded OD, no assignment prior | 0.09 (also measured as 0.093 in an earlier run) |
| Grounded OD + assignment prior (bound form) | **0.154** (also 0.15 in the demand-level A/B) |

A genuine, reproducible **+65-70% relative improvement**, achieved with a
mechanistically understood, generalisable fix — not a tuned coincidence.
GENERALISATION TO NEW SENSORS: the assignment field is recomputed from
population/POI/network structure alone (no per-sensor tuning); only the
ONE scale factor is fit against measured edges, so more sensors -> a more
robust fit (and a natural extension to per-road-class factors), while the
sensors ALSO directly tighten level-2 bounds and unlock more corridor
couplings — three independent mechanisms all strengthening together as
the city adds stations, none requiring retraining.
STALE AS OF 2026-07-09 — see the PENDING RE-VALIDATION note in section C:
these figures predate E-I/I-E tours and hour/day-type-aware purpose
sampling.

### D — Forecast (`train_agent1.py`, `build_agent1_flows.py`) — built
Per-station baseline + holiday factors; beats seasonal-naïve +12–29 %.
For future dates, C consumes D's series instead of history — same code path.

### E — Simulation (`build_sumo_net.py`, `run_scenario.py`, `serve.py`) — built
SUMO **mesoscopic** Monte Carlo (43× faster than micro at equal/better
sensor delivery, 0.87–0.96), identical edge IDs, local rerouters for
closures, interactive API. Gate: baseline delivery ≥ 0.85 at stations.

Recurring monthly closure searches now have a separate internal screening
path: `traffic_sim/simulation/monthly_proxy.py` ranks exact calendar
schedules from explicitly projected forecast/structural flow and detour
reserve, `proxy_projection.py` preserves the sparse-sensor evidence boundary,
and `proxy_validation.py` owns the frozen exhaustive-SUMO release gate.
Matched finalist decisions are isolated in
`traffic_sim/simulation/pilot_selection.py` and
`traffic_sim/simulation/finalist_decision.py`: the former can only retain a
pre-registered band of matched worst-variant mesoscopic pilot contenders and
can never publish a winner; the latter keeps q10/q50/q90 separate,
each observation is paired to the same seed and baseline, hard failures are
removed before ranking, and the primary score is the worst-variant upper
simultaneous 95% bound on added SUMO `timeLoss`. The result contract is
fail-closed (`unique_winner`, `tie`, `inconclusive`, or `no_viable`) and
returns exact candidate/variant repetition requests when more paired seeds
are required. `traffic_sim/simulation/micro_confirmation.py` keeps
conditional bounded microscopic evidence in a separate module, status and
provenance; it is never mixed into the mesoscopic score, and missing queue
detail is explicitly `queue_detail_not_assessed`.
`screen_monthly_closures.py` and `run_monthly_proxy_validation.py` are
root-level CLI entrypoints only; neither is wired to `serve.py` or the web UI.
Proxy version `monthly_proxy_v1` failed its 2026-07-18 held-out gate
(winner-recall upper bound 0.7778 < 0.90; median Spearman 0.50 < 0.60).
AUDITED then ADOPTION REJECTED (LUNA-V4-04 concluded rejected; LUNA-V5-01
2026-07-27): the v4 audit PASSED — the preserved evidence is complete,
identity-bound and reproduces its report and gate record canonically — but
ADOPTION was rejected for whole-record integrity: a lone gate record is
self-certifying, so any byte edited inside it still validated against
itself. The tracked v4 product candidate was removed and the product is
back in bounded-exhaustive fail-closed mode. Adoption now requires TWO
artifacts (`traffic_sim/simulation/heldout_gate.py`): the gate record AND a
post-review adoption certificate binding its exact bytes, the frozen
manifest identity and the bounded claim scope; absence or alteration of
either fails closed. The audited v4 numbers were
(campaign key `1505ecfb…`, 5/5 cases, 75 schedules): all seven frozen checks
passed — practical-winner recall 1.0 and discriminating practical-winner
recall 1.0 (min 0.9), p90 normalized shortlist regret 0.0 (max 0.1), failure-
disqualification recall 0.6819 (min 0.6), discriminating case fraction 0.6
(min 0.4), ranking case fraction 1.0 (min 0.5), and every shortlist contains
an eligible candidate.  `serve.py` selects proxy screening only behind an
adopted gate and, with none adopted, uses bounded-exhaustive with its hard
cap.  V5 RAN (2026-07-27) and FAILED discrimination: all five held-out edges
produced zero objective spread, so no case discriminated and the gate failed
`discriminating_case_coverage` and `discriminating_practical_winner_recall`.
That was a property of the selected edges, not proxy quality: v5's structural
selection had no pre-outcome signal for objective spread. Its evidence is spent
and treated as opaque.
V6 is FROZEN but UNEXECUTED and UNAPPROVED
(`validation/monthly_proxy_manifest_v6.json`): five cases and 75 schedules on
edges disjoint from every v1-v5 held-out edge and their junction neighbours,
selected by the versioned `demand_exposure_v1` rule from the CANONICAL archived
demand designated for v6 — strictly positive q10/q50/q90 exposure in every
closure window, ranked by temporal variation — with v4/v5 gate thresholds
unchanged. That archive designation is v6-LOCAL and does not repair the
globally ambiguous demand key `2ac04275daabe93c`, which three successful
archives claim with three distinct input contents. Demand exposure is a
selection signal only; it does not guarantee a 300-second SUMO spread. Each
case preserves its RAW evidence — the 15 frozen schedule IDs and the q10/q50/q90
vehicle exposure for each of those windows — so the aggregates and the whole
ranking recompute from the artifact alone, without rerunning the freeze. The
frozen package has no overwrite escape hatch and publishes all-or-nothing. The
no-overwrite guarantee comes from the publishing PRIMITIVE, not from a check
that precedes it: scratch files are created `O_EXCL` and finals are published
with `os.link`, which fails if the path already exists, so a final appearing
between an absence check and the write cannot be clobbered. This call owns only
what it created — a foreign file at a final path is preserved and refused over,
never deleted — and a rollback that cannot clean up raises instead of reporting
success. Any v6 SUMO execution requires a clear user request for that frozen
campaign and the normal safety confirmation appropriate to an expensive,
evidence-producing run; it is not tied to a particular model role.
MONTHLY WARM-STATE ACCOUNTING (LUNA-WARM-04, 2026-07-28): the warm branch's
pre-warm segment is described by versioned `monthly_prefix_evidence_v1`:
completed-only trip aggregates, queue maximum, counters and recovery buckets,
stored inside the warm-state cache entry's atomic digest-bound member set. Every
`DisruptionMetrics` field crosses the warm boundary by an explicit rule bound
mechanically to `dataclasses.fields`, so an unclassified field is a hard error
rather than a silently dropped or double-counted value. Recovery buckets are
concatenated into one ordered, gap-free domain and never synthesised. This is
accounting only. v1 was EXECUTED (LUNA-WARM-05) and FAILED: SUMO's
`loaded`/`inserted`/teleport counters are CUMULATIVE across a loaded state, so
summing prefix and post double-counted every vehicle live at the snapshot
(+1081/+1065); closure throughput was never measured on the warm side (0 vs
None); and the prefix had been simulated from the UNFILTERED route. v2
(LUNA-WARM-06) takes cumulative counters from the post state with the prefix as
a lower bound, measures closure throughput with closed edges zero-filled, and
selects the snapshot strictly before the earliest departure that closure
filtering changes or drops — audited per variant against the archive routes and
frozen into the campaign identity.

v2 was EXECUTED (LUNA-WARM-07) and FAILED, but far more narrowly: 3/3 coverage,
complete execution evidence, and 16 of 18 semantic groups exactly equal. The
single residual group was the objective itself, `total_time_loss_s`, where warm
came out LOWER on every identity — q10 -7.73 s, q50 -80.62 s, q90 -138.97 s —
monotone in demand volume. That ordering identified the cause: a vehicle still
driving at the snapshot is BOUNDARY-ACTIVE, excluded from the completed-only
prefix and, after `--load-state`, reporting only post-boundary time loss in its
resumed tripinfo, so its pre-boundary delay was counted nowhere. Completed-only
tripinfo had removed double-counting and introduced under-counting instead;
denser demand strands more vehicles at the warm point, which is exactly the
observed pattern.

v3 (LUNA-WARM-08) fixes the accounting rather than tolerating the gap.
`warm_state_boundary.py`'s `WarmPrefixController` owns ONE SUMO process and its
TraCI connection for the prefix run, so the ledger and the saved state describe
the same instant of the same run — a batch `--save-state` run cannot provide
that, because by the time it exits there is nothing left to ask which vehicles
were in flight. `traci`, `subprocess` and `socket` are imported LAZILY inside
its methods, so constructing a controller starts nothing and the validation
harness can hold one unconditionally while every check stays process-free. It
captures a per-vehicle ledger of exactly the vehicles in flight AT the saved
step and reconciles it against the resumed tripinfo BY VEHICLE IDENTITY:
`monthly_prefix_evidence_v2` carries the ledger and the per-vehicle completed
map, and each boundary vehicle contributes its post-warm trip plus its ledger
offset exactly once. Segment values stay RAW until the end and are normalised
ONCE per final per-vehicle total: rounding each half separately is a different
operation from rounding the whole, so a vehicle accruing 1.005 s on each side of
the snapshot would reconstruct as 2.00 while one uninterrupted run reports 2.01.
Raw in memory is not sufficient, because the post-warm half round-trips through
a FILE that SUMO writes at its reported precision — and that residual has NO
precision-based fix. Raising SUMO's output precision was tried and reverted for
two independent, measured reasons: `--precision` is GLOBAL, so it also changed
the warm arm's edgeData and summary output and broke the recovery and waiting
semantics the contract requires to stay identical; and no finite precision
suffices anyway, since for any p the true sum can sit closer to a rounding
boundary than the serialization error at p (proven for p = 2..12 in
`tests/test_warm_state_boundary.py`). Warm argv is therefore BYTE-IDENTICAL to
cold argv, the comparison stays exact with no tolerance, and the residual is
declared in the frozen manifest as `known_residual`: at most one unit in the
last reported place per boundary vehicle. It can therefore make the campaign
FAIL rather than pass quietly — which is the honest outcome, and closing it
needs a decision above the accounting layer.
The per-vehicle completed map must also AGREE with the aggregates it accompanies
— same trip count, same total, disjoint from the active set — on write and on
read, since otherwise the objective is rebuilt from one while every other check
reads the other. Capture at any step other than the warm point is fatal,
because it would name the wrong vehicles and every later reconciliation would
be confidently wrong. A boundary vehicle that never reappears, a vehicle in
both segments, a duplicate or malformed tripinfo record, and legacy v1 evidence
are all fail-closed; v1 evidence is a cache MISS, never reinterpreted. Every one
of those failures becomes a COLD FALLBACK with a recorded reason rather than an
escaping error — the guard sits at the boundary in `_run_observation` that
consumes the warm path, so it does not depend on having predicted which internal
step fails. The warm arm is an optimisation over an equivalent cold arm, so
nothing it does may cost an observation: an escape would abort the whole
identity and produce no evidence at all, which is strictly worse than running
cold. The
boundary schema and the tripinfo precision are bound into the warm identity by
content, so changing either invalidates cached prefixes instead of silently
altering reconstructed objectives. Split diagnostics publish only BOUNDED
facts — a count, a digest and the reconciliation totals — never the per-vehicle
map, which would grow the canonical payload with traffic.

Cold/warm equivalence and any speedup remain UNDEMONSTRATED; v3 is frozen,
unapproved and unexecuted, and it disclaims its own hypothesis in advance: if
the objective still differs after reconciliation, boundary-active accounting is
NOT the cause and the campaign fails honestly. v1 and v2 are retained for
provenance and are permanently unadoptable — their frozen source fingerprints
predate this accounting, and that drift is the mechanism that blocks reuse.

WARMING HAS NEVER EXECUTED — DIAGNOSED AND REPAIRED (LUNA-WARM-14/15,
2026-07-31). The v6 campaign ran and its structured diagnostics named the cause:
every identity recorded `cache_miss -> bootstrap_started -> snapshot_failed
[No module named 'traci', ModuleNotFoundError] -> bootstrap_failed`. Production
did a bare `import traci`, but the package ships INSIDE the SUMO installation at
`<sumo_home>/tools/traci` and is not on `sys.path`. So every "warm" arm since the
controller was introduced was a cold fallback caused by an import error, and
warm_executions was 0 in v4, v5 and v6 alike. (That changed at v9, which warmed
all three identities — see the localization section below. No equivalence,
speedup or adoption claim rests on it: v9 failed.)

The repair lives in `traffic_sim/simulation/runtime.py`: one resolver imports
TraCI from the exact active SUMO home (a non-empty `SUMO_HOME` wins
deterministically; a declared-but-unusable home is an error, never a silent
fallback to a different installation) and PROVES the imported module's origin
lies inside that installation's `traci` package. The controller resolves through
it before any launcher or port, so an unusable TraCI starts no process; and the
campaign harness runs the same resolver as a mandatory preflight AFTER
approval-token validation but BEFORE any artifact root is checked or created, so
an environment that cannot warm can never again consume an approved,
non-resumable campaign.

The check that would have caught this without a campaign now exists: the resolver
is exercised against a real fake `<home>/tools/traci` package through Python's own
import machinery. Every earlier test injected a fake traci module and therefore
could not notice that production never resolved one — the same unwired-seam shape
that produced the boundary controller, default-runner and unforwarded-attempt
defects. A dependency a test supplies is a dependency that test cannot check.

Confirmed once by a direct import-only probe of the installed package under audit
-event guards (no socket, no child process, no TraCI call): origin
`<sumo_home>/tools/traci/__init__.py`, full required API present.

v7 was REJECTED in process-free review and never approved or executed: its
resolver repair was correct, but its fingerprints omitted
`tests/test_warm_state_boundary.py` and `tests/test_monthly_warm_state.py`, so
the two regressions that give the repair its meaning could have been weakened
while its key still validated. The rejection cost no campaign. v7's bytes are
preserved exactly as reviewed; superseded contracts are succeeded, never edited.

WARM EXECUTION HAPPENED, AND THE RESIDUAL IS NOW LOCALIZED (LUNA-WARM-16/22/23,
2026-08-01/02). v9 was executed once with exact user approval and is the FIRST
campaign in this family whose warm arm actually ran: all three identities reached
`warm_completed`. It then FAILED the paired comparison — 3 comparisons, 3
mismatches, no cache published — with a residual of -7.73 / -80.62 / -138.97 s.
Those numbers are bit-identical to v2's, which predates `--save-state.rng` and
`--save-state.precision` entirely, so the state-serialization hypothesis is
REFUTED. Warm was also ~13% slower than cold there, as expected while every
identity is a cache miss paying for its own prefix.

The LUNA-WARM-22 forensic diagnostic then localized the gap exactly. It is NOT
distributed, NOT rounding and NOT a partition error: 5 of 44, 10 of 50 and 12 of
51 vehicles IN FLIGHT across the warm point carry the entire residual between
them, their per-vehicle deltas summing to it to the cent. All are negative, all
sit in the resumed phase, and most — 1, 8 and 10 respectively — come back with
EXACTLY 0.0 accumulated time loss. Every other vehicle, more than 99.99% of the
population, is identical between the arms.

That refutes BOTH earlier rules at once. LUNA-WARM-08 probed one boundary vehicle
and found its accumulator preserved; generalising that to a universal rule loses
the minority that IS the residual. v3's blanket per-vehicle offset would double
count the ~80% of in-flight vehicles whose accumulator demonstrably survives.
WHY those particular vehicles lose it is still unknown, and no evidence so far
explains the selection.

v10 (LUNA-WARM-23) binds a SELECTIVE, restore-measured correction instead of
either rule: the prefix controller captures a `vehicle_id -> timeLoss` ledger
from the same TraCI connection, instant and process that writes the state; a
bounded resumed controller connects before any resumed step and captures the same
map after the load; and only the positive `saved - restored` differences actually
observed are added back, once each, to their own resumed record at production
reporting precision. Nothing is inferred — an unmeasured vehicle is never
corrected, and a preserved one is left byte-semantically unchanged. Prefix
evidence advances to `monthly_prefix_evidence_v4` to carry the audit.

v12 was approved and EXECUTED once (LUNA-WARM-24). All three warm arms completed,
but the exact v2/v9 residual remained: cold minus warm was 7.730000004,
80.620000002 and 138.970000003 seconds for q10/q50/q90, and no cache was
published. Its equal TraCI save/restore ledgers therefore REFUTE the selective
TraCI-deficit correction rather than validate it.

V13 (LUNA-WARM-25) follows the actual SUMO mesoscopic source. Tripinfo reports
the private `MSDevice_Tripinfo::myMesoTimeLoss` member and device save/load omit
that member. Although TraCI documents `getTimeLoss()` as accumulated time loss,
the frozen mesoscopic save/load diagnostics show it does not exactly reproduce
the private tripinfo member across this boundary. The state-owning prefix process now emits
`tripinfo-output.write-unfinished=true` at precision 16 after capturing the
exact active ID set. Strict parsing binds those unfinished values to that set;
the resumed phase joins the private prefix and resumed accumulators by vehicle
ID and rounds every whole trip once to the ordinary two-decimal production
format. The ledger validator reconstructs the captured-population digest from
the warm point and sorted active IDs on every read. Completed prefix XML order
is persisted separately from its canonical ID map, and the resumed values
continue that exact accumulator so floating-point segment regrouping cannot
create a cold/warm mismatch. Completed, active and post-boundary populations
must be exhaustive and disjoint. Missing, duplicate, overlapping, malformed or
unknown pre-boundary records fail closed to the unchanged cold path. Because
SUMO precision is
global, warm edgeData is normalized per edge back to cold precision before
recovery aggregation; integer queue, counter, health and closure fields keep
their existing parsers. V13 no longer retains all arrived vehicles: terminal
TraCI ledgers are retired, so that memory/runtime cost provides no evidence.

The warm cache identity advances to schema 2 and prefix evidence to
`monthly_prefix_evidence_v7`; every legacy entry is a cache miss. V13 was
executed once, but the execution sandbox denied the IPv4/TCP localhost bind in
`WarmPrefixController._free_port()` before every prefix launch. All three
attempts therefore fell back to cold, produced zero semantic mismatches but zero
valid warm executions, and published no cache. This is environment-failure
evidence, not an equivalence result. V14 preserves the complete v13 experiment
and mechanism but adds a mandatory bind-capability preflight after approval and
TraCI validation and before keyed-root inspection. V14 is FROZEN, UNAPPROVED
and UNEXECUTED. Product-default warming remains OFF; the shortest remaining gate
is one fresh exact-key campaign run once with approved socket permission.

PRESERVED-ACCUMULATOR WARMING, v4 FROZEN (LUNA-WARM-09, 2026-07-30): the
measurement below retired v3's design, and v4 replaces it. The objective rule is
now the completed-prefix aggregate PLUS the resumed aggregate — each vehicle
counted once and whole — with NO per-vehicle boundary offset anywhere. A vehicle
that finished before the snapshot is in the prefix aggregate; one still driving at
it is absent from the completed-only prefix and appears once in the resumed
aggregate carrying its full time loss, because the saved state preserved its
accumulator. Every other `DisruptionMetrics` field keeps its existing explicit
rule. Prefix evidence advances to `monthly_prefix_evidence_v3`, which carries
bounded snapshot facts instead of a per-vehicle ledger; v1 and v2 evidence are
cache MISSES, never reinterpreted.

A welcome consequence: with no sum-of-halves there is no serialization residual,
so the bounded ±0.01 s skew v3 had to declare — and could never eliminate at any
output precision — simply does not arise.

THE SNAPSHOT NOW APPLIES WHAT ITS IDENTITY CLAIMS. v3's cache identity recorded
`save-state.rng` and 16-digit precision while its prefix command applied neither,
so the state on disk was not the state its key described. The prefix command now
carries exactly one `--save-state.rng true` and one `--save-state.precision 16`,
DERIVED from the same `warm_state_cache` constants the identity records rather
than restated by hand, and the controller refuses a command that omits or
duplicates either, or that sets the global `--precision` (which would change
edgeData and summary output and break the warm arm's recovery and waiting
semantics). `WarmPrefixController` owns one process and one connection, snapshots
at the exact step, requires an observed zero exit, captures stderr to a file
rather than a pipe, and kills/reaps without masking a primary error.

WHAT REMAINS UNPROVEN, stated plainly: LUNA-WARM-07's residual is UNEXPLAINED,
not explained. Its boundary-active explanation was refuted by measurement. The v4
hypothesis is that DEFAULT state serialization caused it, and v4's frozen manifest
records that hypothesis, the exact mechanism under test, and the condition that
refutes it — if the objective still differs with these settings applied, the
cause is something else and the campaign fails honestly. Warming remains
default-OFF; v4 is frozen, unapproved and unexecuted, and the one remaining gate
is a single fresh approved paired cold/warm campaign. v1, v2 and v3 are retained
byte-untouched and are all unadoptable: their frozen fingerprints predate this
accounting, and v3's freeze tool can no longer even import against the live tree.

SAVED-STATE TIME-LOSS SEMANTICS MEASURED (LUNA-WARM-08 revision 3, 2026-07-30,
`validation/warm_state_time_loss_semantics_v2_outcome`): one approved
non-campaign SUMO/TraCI diagnostic against the tracked network answered the
question v3 was built on top of, and the answer CONTRADICTS v3's premise.

The revision-2 run reached the same numbers but was REJECTED rather than
reinterpreted: its arms waited for each SUMO process and discarded the result, so
a run that completed and then failed was indistinguishable from a clean one. This
revision requires and records an observed zero exit from all three arms — cold 0,
prefix 0, resumed 0 — and the classification reproduced unchanged, which means
those numbers were never wrong, only unverified. That distinction is the whole
point: unverified evidence is not evidence.

Fixture: one synthetic vehicle on the deterministically-selected probe edge
`10017905051_124601298_0` (271.1 m, limit 13.89 m/s, chosen as the
lexicographically smallest of 737 qualifying edges), speed held at 2.0 m/s at
every step in every arm, snapshot at t = 20 s, SUMO 1.27.1, default output
precision throughout. Three runs: one uninterrupted, one prefix that captured
the boundary and saved state through the same TraCI connection, one resumed.

Observed: boundary `getTimeLoss` 15.7184 s; immediately after `--load-state` the
restored vehicle reports 15.72 s — the accumulator SURVIVES. The resumed run's
tripinfo then reports `timeLoss` 109.90 s, exactly equal to the uninterrupted
run's 109.90 s, and NOT equal to 109.90 − 15.72 = 94.18 s. The resumed tripinfo
record is in fact identical to the cold one field for field, including
`depart="0.00"` and `arrival="128.00"`. Classification:
`full_accumulator_preserved`.

CONSEQUENCE for the v3 accounting: a resumed vehicle's tripinfo already carries
its whole trip's time loss, so adding a per-vehicle boundary ledger offset on top
of it DOUBLE COUNTS the pre-boundary delay. v3's reconciliation is built on the
opposite assumption. The v3 artifacts are deliberately left untouched by this
revision, and choosing the replacement accounting is not this task's decision.

CONSEQUENCE for the original observation: this also reopens LUNA-WARM-07's
measured gap. If resumed tripinfo is complete, then completed-only prefix plus
resumed already sums correctly with no offset, so the −7.73 / −80.62 / −138.97 s
shortfall — monotone in demand — must have a DIFFERENT cause that remains
unexplained. It was never proven to be boundary-active accounting; it was only
consistent with it.

LIMITATIONS, and they are real: one synthetic vehicle, one edge, no interacting
traffic, one SUMO version, one platform, one snapshot instant. All three arms
logged a benign SUMO warning that `SUMO_HOME` was unset, which disables input XML
schema validation; the network was already validated when it was built, and the
warning is recorded in the outcome rather than suppressed. This is a
MECHANISM observation, not equivalence evidence, not performance evidence, and
not grounds for adoption or release. A single vehicle cannot show what happens to
a vehicle that is mid-junction, mid-lane-change, teleporting, or queued behind
others at the snapshot.

LIMITATION, deliberately recorded: the proxy is validated as a SHORTLISTER,
NOT as a full ranker.  Median Spearman is NEGATIVE (-0.371 overall, -0.637 on
the discriminating cases); v4's gate scores practical-winner recall, regret
and failure recall, and keeps Spearman as a diagnostic only.  Claims stay
limited to SUMO-verified schedules inside the enumerated search space, and
failure-disqualification recall sits only modestly above its 0.60 floor.
The 2026-07-19 `stratified_shortlist_v2` safety correction does not relabel
that failed evidence: bounded searches select every legal schedule even when
the proxy cannot score it, and larger searches explicitly simulate
unscoreable controls when capacity permits and withhold whenever any such
candidate is omitted.  The release path is analytical ordering → matched
q10/q50/q90 mesoscopic pilot → adaptive robust finalists; its policies still
require a named golden benchmark and a new untouched held-out validation set.

The existing single-day closure-time entrypoint now exercises the safe
decision seam end to end. `suggest_closure_time.py` uses one matched
q50/q10/q90 pilot replication per candidate, applies all closure-integrity
gates before finalist selection, and gives retained finalists four matched
replications per variant (12 runs) for health/integrity evidence. Pilot and
final ranking use the same deterministic closure-cost objective: field-wise
worst added vehicle-hours across q10/q50/q90, then added metres and affected
vehicles as exact lexicographic tie-breakers; any no-detour vehicle
disqualifies the schedule. Missing objective evidence fails closed rather
than falling back to sampled time loss.
`serve.py` rejects old three-seed or non-canonical structured requests, and
the web UI distinguishes a robust finalist winner, a practical tie,
inconclusive evidence and no viable closure.  A real Gothenburg/SUMO 1.27.1
smoke case is frozen in
`validation/robust_closure_search_smoke_v1.json`.  This proves the runtime
path and its fail-closed result contract; it is not the resumable multi-day
monthly orchestrator and cannot lift the global-best gate above.

The resumable monthly execution seam is now built separately under
`traffic_sim/simulation/monthly_search.py`, with
`run_monthly_closure_search.py` as its root CLI and
`monthly_sumo.py` as the archived-demand SUMO backend.  Every stage publishes
an immutable, hashed workspace artifact: policy, exact schedule ledger,
screening, per-candidate pilot evidence, pilot selection, cumulative
adaptive finalist evidence, each robust decision round, complete backend
provenance and the final result. A process restart skips completed
candidates; a changed search, policy, demand/backend identity, source digest
or non-canonical seed mapping fails closed. Candidates from different
calendar envelopes may correctly use different matched baseline IDs, while
candidates inside the same envelope must still share byte-identical
baseline/provenance for each variant/seed.

`validation/golden_monthly_search_v1.json` freezes the first internal
execution policy: one q10/q50/q90 pilot replication, a 300 s retention band,
four initial finalist repetitions, a 600 s absolute precision floor, 300 s
practical equivalence and a cap of 12 repetitions per variant.  The bounded
three-window Gothenburg/SUMO 1.27.1 golden completed in 211.42 s at
427,819,008 bytes peak RSS; the sole viable schedule reached precision after
q10/q50/q90 counts 4/5/7. This validates execution, restart and policy
resources only. The backend currently consumes one explicit immutable demand
archive covering the shortlist envelopes; automatic build/cache resolution
across all date envelopes in a future month remains required. The untouched
monthly held-out gate HAS now run: the v4 record passed its audit on
2026-07-27, but its ADOPTION WAS REJECTED for whole-record integrity (see the
monthly-proxy section above), so global-best and UI exposure remain FALSE.
They can open only once a future campaign's record is adopted together with a
post-review adoption certificate, and then only for SUMO-verified schedules
within the enumerated search space.

The objective-alignment migration is explicit and does not rewrite that v1
golden identity. `validation/monthly_search_policy_v2.json` is provisional and
binds `closure_cost_v1`. The monthly SUMO backend emits one deterministic
disruption record per demand variant; independent-day execution preserves the
records through worker/cache serialization and sums them per variant before
ranking a multi-day parent. Both pilot selection and finalist decision receive
the policy's same `objective_method`. The v1 policy remains the legacy
time-loss path until v2 receives a named benchmark and new validation evidence;
the production/UI gate is therefore unchanged.

`validation/monthly_search_v2_benchmark_v1.json` records the first isolated
v2 execution checkpoint. Its tracked plan pins the original golden spec,
policy, archive routes, network and runner by SHA-256; the runner bypasses the
mutable demand resolver and writes only to a dedicated cold/serial search and
cache root. The run completed in 318.18 s, produced a unique 06:30–10:30 result
and reloaded byte-identically. This is diagnostic, not a policy freeze: the two
lower-cost windows failed the teleport hard gate, so only one candidate was
viable and no practical-equivalence tolerance could be calibrated.

#### Read-only search preflight (`closure_preflight.py`) — PR B, BUILT

`traffic_sim/simulation/closure_preflight.py` sizes a rolling closure search
EXACTLY, before any job exists, without materializing a single
`ClosureSchedule`.  The versioned `ClosureSearchPreflight` contract
(`closure_search_preflight_v1`) reports valid workday counts, parent schedules
grouped by workday count, unique `(date, start, end, road)` daily units, units
whose warm-up or recovery falls outside the downloaded demand year, known cache
hits/misses, `cache_unknown`, an estimated SUMO workload with its estimation
basis, and a `size_class` of `normal`, `large_but_runnable` or
`over_resource_budget`.

It reproduces the production calendar semantics rather than approximating them:
15-minute alignment, the configured timezone and DST policy, allowed weekdays,
blackout dates, exact equal daily shifts, up to 90 workdays, rolling periods
that may cross weeks, months and years, and one identical start/end clock time
on every selected workday. `ClosureSearchSpec.min_consecutive_start_days`
defaults to one and is omitted from the content payload at that default, so
legacy search identities remain stable. A non-default minimum restricts both
the generator and exact preflight; setting minimum equal to maximum requests an
exact workday count instead of paying to evaluate shorter periods. Counting is
a run-length identity — a maximal run
of `L` usable dates contains `max(0, L - n + 1)` windows of length `n` — which
is why it needs no objects.  `equal_daily_rounded_v1` counts on the
calendar-date axis and `exact_equal_daily_v1` on the eligible-date axis,
because the generator advances differently under each.  Exactness is
differential-tested against `generate_closure_schedules` and
`decompose_schedules`, including 40 randomized contracts, year boundaries, leap
days, both DST transitions, blackouts, overnight bands and the plan's
07:30–15:15 closure inside a 06:00–18:00 band.  `exact_balanced_daily_v1`
creates up to 4096 duration patterns whose validity varies by position inside
the window; it is REFUSED (`UnsupportedPreflightSpec`) rather than approximated.

`POST /api/monthly_search/preflight` exposes it and is strictly read-only: it
builds no demand, starts no SUMO, creates no job, spec file, run, cache or
evidence artifact, and takes no simulation lock — so an estimate can be
obtained while another job runs.  The daily backend identity that the result
cache is keyed on only exists after the demand resolver has prepared an
archive, which a read-only call must not do, so the endpoint reports
`cache_unknown` rather than an invented miss.  The web UI shows the estimate
before start, refuses to launch an `over_resource_budget` search, and leaves
the date range, requested work hours and workday cap editable.  The existing
100,000-parent and 10,000-unit caps are REPORTED, never raised or bypassed.

Measured on the plan's two documented six-month cases, and matching their
recorded sizes exactly (2,186/5,676 and 11,813/23,349): preflight p95 0.0147 s
at 16.4 MiB and 0.0514 s at 21.8 MiB, against 2.90 s/175.5 MiB and 12.10 s/
489.9 MiB to materialize the same searches. Both PR B exit gates (p95 ≤ 3 s,
peak RSS ≤ 32 MiB) pass.  `validation/closure_search_scaling_baseline_v1.json`
(PR A) freezes those references with exact Python, SUMO, network, route, policy
and source identities; it is diagnostic baseline evidence and opens no gate.
Its opt-in external arm also measures q10/q50/q90 deterministic disruption
(p95 12.799 s) and one q50 SUMO daily unit (p95 9.040 s), five repetitions
each. Diagnostic SUMO outputs live only in a private temporary root and are
removed after every repetition. Known cache counts exclude units whose exact
envelope cannot execute inside the demand year.

#### Streaming closure ledgers (`closure_ledgers.py`) — PR C, BUILT

The preflight above answers "how big is this search" without objects. PR C
does the same for the search that actually runs.

**The streaming calendar.** `iter_closure_schedules(spec)` yields every legal
schedule lazily, in the identical canonical order.
`generate_closure_schedules(spec)` is now `tuple(iter_closure_schedules(spec))`
— a backward-compatible wrapper, still the API for old callers and small
searches. The enumeration body is unchanged; it yields where it appended. Its
argument type check stays EAGER, so a bad call still fails at the call site
rather than at first iteration. Byte equivalence with the pre-PR-C generator is
pinned by frozen `to_dict()` digests over five contract shapes
(`tests/test_closure_calendar.py`), and ledger bytes are re-derived under three
`PYTHONHASHSEED` values in real child interpreters, because an in-process test
cannot change string hashing after start-up.

**Ownership.** A search's enumeration lives in exactly one place:
`runs/closure-search/<search_id>/ledgers/`, written by
`traffic_sim/simulation/closure_ledgers.py` and owned by that search alone.
Three NDJSON files, one canonical row each:

    parents.ndjson       one row per parent ClosureSchedule
    units.ndjson         one row per UNIQUE daily unit
    parent_units.ndjson  one row per parent: its ordered daily-unit IDs

The reverse unit→parents graph is GONE from this path. It was only ever the
inverse of `parent_units.ndjson`, and materializing it cost memory proportional
to parents×days rather than to either. `DailyClosureUnit` keeps its
`parent_schedule_ids` for v1 callers, caches and workspaces; the streaming path
uses `StreamingDailyUnit`, which carries `unit_id`, `schedule` and `identity`
and nothing else. Both get their identity from ONE implementation,
`independent_daily.daily_unit_records`, so a streamed unit ID can never drift
from a cached one — two implementations of a content-addressed ID is exactly
how a cache silently stops hitting, and nothing would report it. That function
returns the unit's schedule behind a deferred `build` callable, because a
parent contributes one record per interval while only a UNIQUE unit needs a
schedule object: 171,880 records against 5,676 units on the 720-hour case, at
85 µs to build against 11 µs for the identity. Building eagerly made
`decompose_schedules` five times more expensive for v1 callers; every caller
now builds exactly where it deduplicates.

**Versioning.** `closure_search_ledgers_v1`, version 1, declared in the
manifest and again in the workspace artifact's provenance
(`ledger_schema`, `ledger_version`, `ledger_directory`,
`ledger_manifest_content_key`). A future schema refuses an old directory
instead of misreading it.

**Atomic publication, manifest last.** Every ledger is written to a
`.partial` file in the same directory, flushed, fsynced, `os.replace`d into
place, and the directory itself fsynced. `ledgers.manifest.json` — schema,
version, search content key, provenance, per-file rows/bytes/SHA-256, the three
counts, status and its own content key — is published LAST and atomically. Its
presence is the completion signal.

**Two failure modes, deliberately different.** No manifest raises
`LedgerIncomplete`: nothing ever declared the ledgers finished, so rebuilding
is safe and correct. A manifest that does not match its ledgers raises
`LedgerCorrupt` and the caller stops. Size, SHA-256 AND row count are all
checked — size alone misses an equal-length corruption, the digest alone does
not localize a truncation, and the row count is what a caller actually
iterates. A completed ledger that no longer matches its own digest is not a
rebuildable scratch file; regenerating it would destroy the only evidence that
something damaged a frozen artifact.

**Restart and compatibility.** `monthly_search._candidate_ledger` opens a
search's enumeration in three states, in priority order: a pre-PR-C
`closure_schedule_ledger` artifact (`candidate-ledger.json`) is read exactly as
it was written, so old workspaces stay resumable; a published streaming
manifest is verified and fails closed; neither means the directory is an
unpublished build area, validated if complete and otherwise rebuilt. The
freeze starts at publication, not at write. Restart is idempotent: a second run
re-reads byte-identical ledgers and publishes no second artifact.

**Only the minimum index.** `ParentLedgerIndex` holds a byte offset per
schedule ID — roughly a hundred bytes each instead of a schedule and its up-to-
90 intervals — and parses one row on demand, so screening membership, pilot
lookups and the period comparison all work through the ordinary
`Mapping[str, ClosureSchedule]` seam they already used.
`IndependentDailyRunner.prepare_from_ledgers(directory, parent_ids)` reads only
the shortlisted parents' relationships and only the units those reference.
A backend without `prepare_from_ledgers` still gets a materialized shortlist,
but explicitly and boundedly: above `MATERIALISED_SHORTLIST_LIMIT` (512) it
raises instead of quietly allocating, because a silent fallback is how a memory
gate stops meaning anything.

The product CLI runs the exact read-only preflight before an independent-
exhaustive search reaches network fingerprinting, runner construction or the
monthly search workspace. Supported allocation policies therefore refuse an
over-budget parent/unit population before writing the candidate ledgers. The
legacy balanced allocation policy remains stream-compatible where its exact
closed-form preflight is intentionally unsupported; its caps are enforced
during streaming. Whenever an exact preflight exists, its final parent and
unique-unit counts must equal the streamed enumeration or the run stops.

**Measured (`validation/closure_search_streaming_v1.json`, PR C).** A separate
diagnostic comparison record; PR A's baseline is NOT rewritten and now
correctly reports source drift on the four files PR C changed. Every frozen
case is run twice in fresh child interpreters — v1 materialization and the
streaming writer — on the same host in the same session, because a resident-
memory figure from one operating system is not evidence about another. See the
plan's PR C section for the numbers and for the exact status of the plan's
under-64-MiB exit gate.

**Import cost is part of the contract.** The gate is a claim about a PROCESS,
so what the process imports is part of what it measures. `finalist_decision`
needs SciPy for exactly one t-quantile inside a confidence half-width, but it
imported it at module scope, and `independent_daily` imports
`finalist_decision` — so enumeration, preflight, ledger writing and cost
ordering each paid 81.7 MiB for a distribution they never evaluate. The import
is now lazy (`_student_t_ppf`), with the same call and the same numerics.
`approved_seed_workers` and `SEED_WORKER_BENCHMARK_RECORD` moved unchanged into
`traffic_sim/simulation/seed_worker_budget.py`, a module with no simulation
dependency, and `monthly_sumo` re-exports both; the closure-search CLI reads
the budget, the spec, the policy and the exact preflight before importing the
SUMO stack at all, so an over-budget search is refused in about 22 MiB instead
of after ~110 MiB of numpy/pandas/SciPy and a wait for the shared demand lock.
Measured: the search import chain 99.96 → 21.62 MiB, the CLI 130.60 → 21.68
MiB, and the 720-hour streaming process total ~102 → 23.25 MiB on Linux/x86_64.
`tests/test_search_import_cost.py` pins this in real child interpreters. The
final five-repeat run on the frozen baseline platform (Darwin/arm64, 2026-08-11)
measured 25.30 MiB total with SciPy absent and reports
`memory_gate.status=passed` against the 64 MiB ceiling. PR C's memory gate is
closed.

**Unchanged.** The 100,000-parent and 10,000-unit caps, ranking,
`closure_cost_v1`, pilot selection, finalist decision, teleport policy and
survivability logic are untouched. The 360-hour six-month case is still refused
by the unit cap; it simply no longer fails for want of memory first.

#### Deterministic closure cost before SUMO (`deterministic_disruption.py`) — PR D, BUILT

`closure_cost_v1` is demand-side and congestion-independent: it replays each
calibrated vehicle's baseline route against the closure windows and compares
the cheapest legal path with and without the closure. Nothing in that needs a
simulator, but until PR D it was reachable only through
`ArchivedDemandSumoRunner` — so a search had to simulate a candidate to learn
whether the candidate was worth simulating.

**The headline closure impact is not a SUMO delay result.** The deployed
ranking headline (`added_vehicle_hours`, followed by added metres and affected
vehicles) is produced by the static free-flow graph calculation above. It has
no volume-delay function, dynamic traffic assignment, link-capacity term or
lane-count term. This is deliberate but limiting: the active network has not
shown a stable congestion-delay signal, and the current working baseline has
zero simulated flow on 3,981 of 7,147 published edges (55.7%). SUMO therefore
acts mainly as the candidate's integrity and health verifier — insertion,
unfinished traffic, teleports, closed-edge zero flow and survivability — not
as the producer of the number that orders candidates. A four-lane and a
one-lane edge with equal length and speed have equal per-vehicle free-flow
cost. Capacity-aware ranking requires a separately preregistered and validated
volume-delay or DTA model; adding `numLanes` as an uncalibrated divisor would
not be such a model and must not silently replace `closure_cost_v1`.

The runtime closure rerouter is attached within 400 m of the closed edge for
the production arm. A vehicle already beyond that neighbourhood when the
closure begins may not receive advance rerouting, so this radius limits the
propagation represented by the SUMO verification. `run_scenario.py` exposes
`--rerouter-radius-m` for isolated, timing-bound comparison arms, but changing
the production radius requires paired semantic, health and latency evidence;
the all-network rerouter previously made a whole-day seed roughly an order of
magnitude slower. `--sumo-warnings` can be used in diagnostic runs so route,
departure and rerouter warnings are not hidden by the production log-suppression
default.

`DeterministicDisruptionProvider` is the protocol and exposes no simulation
handle; `ArchiveDisruptionProvider` implements it from one immutable archive
plus `NetworkCostModel`, which parses the network once and carries the digest
it was built from. `MonthlyDemandResolverRunner.archive_for()` and
`.deterministic_disruption_provider()` resolve the right archive and delegate
without starting SUMO.

**Reduction order is a contract.** `sum_daily_disruption` sums a parent's daily
records PER VARIANT and only then takes the field-wise worst. Reducing first
and summing after would let a schedule take its worst day from q10 and its
worst day from q90 and add them — a world in which the direction split changed
overnight. `parent_closure_cost` then uses the unchanged `ClosureCost.sort_key`.

**`vehicles_no_detour` disqualifies; it never becomes a cost.**
`disqualification_evidence` keeps the numbers and the per-variant records so a
pre-SUMO refusal can be read and argued with.

**The daily-cost cache is versioned and content-addressed.**
`deterministic_daily_cost_cache_v1` binds the full daily-unit identity, all
three route files' SHA-256, the network digest, the demand metadata, the
network's validated adjacency-metadata digest, the disruption schema version
and the bytes of every source that computes the number. Route digests are
captured once when the immutable archive is opened; cheap file-state checks
refuse an archive or network that changes beneath an open provider instead of
re-hashing large routes for every daily unit. A widened date range around the
same unit hits; a changed route, network, variant or line of costing code
misses. Concurrent writers publish through unique same-directory partials and
atomic replacement. A record whose stored identity does not match the key it
was found under, an unreadable record or a partial variant set raises rather
than degrading.

**There is one implementation, not two.** `monthly_sumo._closure_disruption`
delegates to the same provider and shares its interval-to-seconds conversion,
so the pre-SUMO and post-SUMO paths cannot drift apart.
`validation/closure_cost_ordering_golden_v1.json` exercises both current paths
against the pinned real q10/q50/q90 golden archive: all fields, historical cost
fields and sorting order agree, and the record reproduces byte-for-byte. PR D's
real-golden equivalence gate is passed; this remains development evidence, not
held-out or release evidence.

#### Cost-ordered verification (`cost_ordered_search.py`) — PR E, the scan

Because the cost is deterministic and the order is total, SUMO can only qualify
or disqualify a candidate — it cannot reorder them. So a run can stop as soon
as no unexamined candidate could still enter the finalist set: deterministically
disqualified candidates never reach SUMO, the rest are verified in
`sort_key` order, a hard failure continues rather than stops the scan, and once
`minimum_finalists` verified candidates are viable the cutoff is the k-th
viable candidate's `added_vehicle_hours`. Verification continues while the next
candidate is `<= cutoff + practical_equivalence_vehicle_hours` — inclusive, the
same comparison `pilot_selection` makes — and stops only when the next
candidate is strictly above it.

The module decides nothing: the finalist set goes to the unchanged
`select_pilot_finalists`, so `ready`, `capacity_exceeded`, `no_viable` and
`incomplete` are untouched and a finalist set can never be silently truncated.
`CostOrderedState` is serializable and its `identity_key` binds policy, cost
ledger and provider identity, so a resume after any of those moved is refused.
The cursor must equal the exact verified prefix, viability must stay in that
order and must agree with the persisted evidence; these checks also run for a
direct dataclass supplied without JSON parsing. Every scan publishes a
machine-readable stop proof naming the band, the first unexamined candidate and
its cost.

#### Cost-first product execution (`cost_ordered_execution.py`) — stage 1, BUILT

The scan above decides WHICH candidates to simulate; this module makes the
product actually obey it. `run_monthly_search(..., cost_source=...)` swaps the
exhaustive pilot for `_cost_ordered_pilot`, and the ordering now runs BEFORE
any SUMO process exists rather than replaying a completed exhaustive record.
Both pilots build their evidence through the same `_pilot_evidence_for`, so the
two paths can differ in WHICH candidates are simulated and never in HOW.

Everything a resume needs is durable. The cost ledger is published once with a
content key that binds the daily-unit identity, the three route digests, the
network digest, the demand metadata, the disruption schema and the costing
source bytes; a cursor is written after every verification. A resume whose
ledger key, bound identity or verified prefix does not match is refused rather
than repaired.

The durable cursor MIRRORS the scan's own bookkeeping instead of reaching into
it: `cost_ordered_search.py` is bound byte-for-byte by
`validation/closure_cost_ordering_golden_v1.json`, so adding a callback to it
would have broken the golden record's source digests. The mirror is
reconstructed from the same inputs under the same rule, and every run asserts
at the end that it never diverged — a fault-injection test sabotages the scan's
returned state to prove the assertion fires.

**Compaction is disabled in this mode, and that is a correctness requirement
rather than a preference.** `IndependentDailyRunner` declares
`compact_pilot_artifacts` because an EXHAUSTIVE independent pilot would write
one JSON file per parent, tens of thousands of them. Cost-first execution
simulates only the boundary set, so the file count is bounded by the finalists —
and without those files a resume cannot prove the cursor's verified prefix, so
every restart of a real cost-ordered search failed closed on "evidence is
missing" until this was fixed. The exhaustive path still compacts.

`reconcile_disruption` is the per-candidate equivalence gate between the
pre-SUMO cost and the post-SUMO evidence: when the runner returns no disruption
records the ledger's are attached, and when it returns its own they must be
field-identical on `vehicles_affected`, `vehicles_no_detour`,
`added_vehicle_hours` and `added_metres_total`.

Every cost-ordered pilot publishes a `cost_ordered_execution_result` artifact —
the exhaustive candidate count against the simulated one, the saving, the stop
proof and the final cursor — and the search result carries a
`cost_ordered_execution` summary, so a reader can tell which execution path
produced a result and what it saved. It carries no wall time or peak RSS
deliberately: a re-entered pilot must reproduce it exactly, and a resume that
reports a different saving raises.

`validation/monthly_search_policy_v3.json` remains provisional and changes
execution order only; its pre-registration freezes every decision parameter and
records that activation, UI exposure and global-best claims remain closed. The
equivalence gate passes on the pinned named golden replay — status and selected
IDs match exhaustive — but saves 0 of 3 verifications because that benchmark
has only one health-viable finalist. **Policy v3 is therefore still inactive**,
and stays inactive until a discriminating benchmark measures a strictly
positive saving and an untouched held-out campaign passes.

#### The discriminating benchmark (`tools/cost_ordered_benchmark.py`)

`--preregister` selects its case from properties knowable before any search
runs and cannot consult an outcome: the tests monkeypatch
`parent_closure_cost` and `ArchiveDisruptionProvider.disruption` to raise, so a
single outcome lookup fails the suite. The registration binds both policies,
the costing sources, the network and its metadata, the three demand variants
per archive, the resource caps, the seeds and the output roots, and freezes
eleven gate thresholds including a strictly positive
`sumo_verifications_saved_minimum`.

**`--from-archives` (v2) discovers the cases instead of guessing them.** v1's
windows were written by hand and mostly named dates no archive held, and its
road was `60786979_3575001205_0` — the documented single-incoming-connection
edge, whose closure severs a successor, so its candidates are degenerate for
reasons unrelated to execution order. Discovery reads `demand_meta.json` only
to propose dates, then asks the product's `MonthlyDemandResolverRunner` and
`find_demand_archives` to validate the exact warm-up envelope, manifest,
generator/runtime fingerprints, provenance and output digests. This matters
because an independent one-day closure normally resolves to a three-day demand
envelope: archive start date and work date are not interchangeable. Individual
dates are offered as well as maximal runs; one day still yields 9–13 legal
start times. Roads come from the frozen topology screen's SURVIVING set. A
discovery that finds nothing refuses to write anything.

**`--run` executes both arms.** Bindings are re-hashed first and all drift is
reported at once; both arms then run under ONE workspace lock held for the whole
benchmark, into separate workspace roots — one root keyed by `search_id` would
let the second arm resume the first's evidence and compare the run with itself.
Both arms are built by `tools/product_arm.py` out of the CLI's own helpers, so a
benchmark cannot measure a lookalike of the product.

Two comparisons matter and only one of them is obvious. The cost-ordered arm
has pilot statistics only for what it simulated — two candidates of forty-five —
so the field-by-field cost gate reads the arm's published cost LEDGER, which
priced every candidate before any SUMO ran, against the exhaustive arm's own
evidence. The stop proof is re-derived rather than trusted: `band_exhausted`
requires the first unexamined candidate to be STRICTLY above cutoff plus
practical equivalence, and (2026-08-28) every bound field of the proof is ALSO
independently recomputed from OTHER published artifacts, not merely re-checked
against itself — `verified_prefix_digest` from the execution cursor and
`evidence_digest` from the complete immutable pilot-candidate artifacts for
the verified prefix (observations and their provenance, disruption, hard
failures and structured timeout identities), the undecided-timeout set from
the same per-candidate evidence (cost-
order v5's exact failure mode: a timeout dropped from the proof while still
present in the evidence), and a band stop's first-unexamined cost against the
published `cost-ledger.json` file. `_stop_proof_valid(execution, arm=...)` in
`tools/cost_ordered_benchmark.py` does the recomputation. It also reconstructs
the k-th viable cutoff from the cursor's verified/viable population and the
integrity-checked ledger, adds the finalist policy's equivalence width to derive
the selection band, and derives schema, examined/total/unexamined counts,
terminal stop reason, first-unexamined identity/cost, proof argument and the
arm's expected `disable_early_stop` value. The cursor's own cutoff and stop
reason must match those independent results. Resealed-execution tamper tests
mutate every proof field in turn, so passing the execution record's self-digest
cannot conceal a changed proof. Fault
injection is part of the run — the probe interrupts a cost-ordered arm, resumes
it, and requires the resumed decision and every candidate cost to match;
skipping it fails the restart gate rather than passing by absence.

**Timeout identity, exact-launch telemetry and semantic-evidence hygiene
(2026-08-28).** A SUMO run that exhausts its registered timeout protocol
produces a versioned structured `TimeoutIdentity` object
(`TIMEOUT_IDENTITY_SCHEMA = "timeout_v3"`) naming the
candidate, daily-unit date, search content key, variant, seed, attempt, fixed
threshold, retry protocol and semantic provenance. Malformed objects and the
retired string formats fail closed on deserialization; historical artifacts
are not rewritten. The v3 wire reader requires exactly the declared field set
and native JSON-compatible types: it never coerces booleans, fractional
attempts or string numerics, validates a canonical ISO work date and a
registered retry protocol, and rejects unknown fields. Historical v3 records
using `single_attempt_fixed_threshold_no_retry_v1` remain readable. Product
monthly execution uses `two_tier_exact_wallclock_v1`: the established 300 s
attempt is followed, only after timeout, by a fresh 1,800 s replay with the
same simulation inputs, seed, worker allocation, routing, teleport policy and
scientific gates. Both launches are recorded separately.
The daily cache and monthly candidate-artifact readers both pass through this
same strict validator. Every real SUMO launch — one (variant, seed)
attempt, success, hard failure or timeout alike — is now counted exactly once
at the actual launch seam inside `ArchivedDemandSumoRunner._observations_for`
(`self.launch_telemetry`, split by pilot/finalist stage), summed across a
study's per-archive runners by `MonthlyDemandResolverRunner.
launch_telemetry_snapshot()`, and surfaced (fail-open, diagnostic-only) as
`exact_launch_telemetry` in `IndependentDailyRunner.timing_snapshot()`'s
existing S0 progress payload. A daily-unit cache hit never reaches this seam,
so it structurally cannot be double-counted as a launch. Isolated workers also
append an fsync'd start record before entering SUMO and a final record after
classification. The parent always consumes this sidecar, even after an
exception or abnormal worker exit; an unmatched start becomes
`worker_terminated`, and retry-local attempt numbers are rebound after the
parent's existing attempt population. `product_arm.run_arm`
publishes that telemetry plus the workspace manifest's own
`active_elapsed_s`/`active_elapsed_basis` (the pre-existing
`awake_monotonic_segments_v1` basis from `search_workspace.py`) in its returned
dict, and `compare_ordered_exhaustive` computes real exact-attempt and
awake-active-time reduction fractions from them — the 30% gates this benchmark
exists to check — instead of approximating from the pilot CANDIDATE count
(kept, renamed, for readability only). Both arms of a comparison now build
under one shared `BENCHMARK_STUDY_PROVENANCE_KEY`, not an arm-name-suffixed
value: that key is stamped onto every `PairedObservation.provenance_key`,
which is semantic evidence content that gets cached and is supposed to be
directly comparable across arms, so giving the two arms different values used
to make their evidence differ by a label baked into content even when the
underlying simulation was identical — confirmed safe because
`IndependentDailyRunner._stable_backend_identity` already strips
`study_provenance_key` before hashing a daily unit's cache key, so unifying it
changes no caching behaviour, only what a `PairedObservation` reads. Which arm
produced a result remains available from `run_arm`'s own `"arm"` field, the
workspace path and the cache-snapshot root — orchestration facts that were
never inside evidence to begin with. Each arm's daily-results SUMO-evidence
cache is still cloned once from one shared initial snapshot into a separate,
non-overlapping root per arm (`_isolated_daily_results_cache_root`); a new
`_assert_fresh_snapshot_pair_matches` additionally asserts the two arms'
pre-run cache digests are equal whenever NEITHER arm's root pre-existed the
call, catching a source that drifted between the two clones, while a genuinely
resumed run reusing its own interrupted arm root is exempt by design.
Candidate evidence is loaded through full workspace-integrity verification;
parse errors, missing expected pilot/finalist stages and incomplete candidate
populations fail closed. Cache comparison uses complete identity-bearing event
records rather than aggregate inequalities alone, and final-decision equality
compares the complete robust-decision payload.

Timeouts are undecided evidence, not traffic-health failures. They are stored
only in `timeout_undecided`; `hard_failures` is reserved for completed checks
that actually establish a disqualification. The monthly runner does not
publish undecided evidence at the first 300 s boundary: it records that launch
and performs the registered exact 1,800 s recovery replay. A timeout becomes
terminal and cacheable only if attempt two also expires. The exhaustive pilot
then stops pending global lookahead because the fail-closed selector is
necessarily inconclusive; completed atomic daily-unit cache entries remain
reusable. Any later explicitly initiated recovery that completes the same
candidate/date/variant/seed identity removes only that matching stale timeout.

Candidate-evidence schema v3 additionally persists one validated
`CanonicalObservationDigest` for every successful canonical SUMO observation.
The record binds candidate/daily-unit identity, work date, variant, seed and
the SHA-256 of the complete canonical observation produced by
`build_monthly_observation`, so baseline/candidate metrics, recovery,
feasibility and health cannot differ while the reduced ranking observation
still happens to match. These records travel in worker-result v3, independent
daily cache v4, daily aggregation, trimming/resume and immutable pilot/finalist
artifacts. The benchmark compares their complete expected stage populations
and requires the cost-ordered population to match the ordered-exhaustive
prefix. Current monthly evidence, daily evidence/cache and worker-result
envelopes require their exact field sets; a missing whole
`timeout_undecided` population or an unknown field is malformed, never
interpreted as an empty timeout set. `MonthlyDemandResolverRunner` exposes
both aggregate launch counters and the complete de-duplicated launch-record
population across every archive runner, validating native identity/outcome
fields and requiring the two views to reconcile before the production runner
chain publishes them.

`--run` writes a SEPARATE outcome record naming the registration by content
key; the registration is never edited, and a run that turns out
non-discriminating is recorded as it happened before a new case is registered.

**Registration schema v3 — what a registration binds.** Two things v2 got wrong
changed what a registration MEANS, so v3 is a new schema rather than new
filenames under the old contract. v2 stays readable, and an outcome speaks its
registration's dialect: replaying a frozen v2 registration still produces a
v2-schema outcome.

* `outcome_record` binds the outcome the CALLER asked for. v2 hard-coded the
  tool's default, so a registration written to a v3 path still named the v2
  outcome. A run now refuses to write an outcome its registration disowns.
* `sources` seals every project module on the arms' real construction and
  execution path — forty-eight files, derived by importing exactly what
  `product_arm.build_arm` and `run_monthly_search` touch — instead of ten
  chosen by hand. The gap was not academic: the runtime correction that bounded
  independent-day cold runs changed `monthly_sumo.py` and
  `suggest_closure_time.py`, and a v2 registration would have reported no drift
  at all. Two of the sealed modules (`heldout_gate.py`, `proxy_validation.py`)
  are imported lazily and decide the claim boundary; a static probe misses them,
  so a test re-derives the closure and fails when a new module appears on it.
Execution errors also publish `failed_execution` with every gate false.
Network and metadata are bound to an explicit data root, both arms execute from
that root, and the shared demand lock is taken there rather than in the source
worktree.

The real v2 run on 2026-08-11 bound 13 schedules on 2027-03-22 to exact demand
build `5ac74750843384b3`. Its first exhaustive SUMO observation timed out after
the unchanged 300-second limit at seed 1000. The outcome therefore opens no
equivalence, saving, held-out or activation gate.

#### Progress vocabulary — step 4, BUILT

`monthly_search.PROGRESS_PHASES` declares the phases in one place —
`policy, preflight, enumerate, screen, cost_units, cost_parents, health_scan,
prepare_backend, pilot, finalists, decide, adaptive_finalists, publish` — and
`SearchWorkspace.update_progress` accepts an optional `detail` mapping,
validated and serialised at write time. `web/app.js` labels every phase and
renders the detail beside it: costed candidates, cache hits, SUMO verifications
and the current cutoff. `tests/test_monthly_progress_contract.py` pins the
search, the API and the UI against each other, so a phase with no label cannot
ship.

#### Independent vs continuous (`tools/measure_independent_vs_continuous.py`)

The harness for PR H's frozen question. It binds to
`validation/independent_vs_continuous_preregistration_v1.json` by content key —
re-deriving each frozen spec's key, so a contract drift is caught rather than
silently measured around — and writes a separate outcome record. It never edits
the registration.

Four buckets stay separate because they are four different facts:
`unsupported_by_contract` (no continuous counterfactual exists above 21
workdays, so there is nothing to measure), `unpairable` (both policies run but
do not describe the same closure), `blocked_missing_demand` (decided by the
product's own `find_demand_archives` against the product's own
`DemandBuildSpec`, per daily unit for the independent arm) and `measured`.
Bucket counts must account for every examined case.

**A third contract finding, measured 2026-08-11.** The registration's
pairability test compares the FIRST schedule each policy enumerates, which is
necessary but not sufficient: 11 of its 35 "pairable" cases search different
spaces, in both directions.

* `equal_daily_rounded_v1` rounds each daily shift UP to the resolution, so the
  continuous arm can serve the same work requirement in FEWER days. The
  21-workday midday case enumerates 17-, 18-, 19-, 20- and 21-day schedules —
  470 candidates against the independent arm's 150 — and the short ones
  schedule up to 5130 minutes for a 5040-minute requirement.
  `exact_equal_daily_v1` cannot express any of them (5040/17 is not a multiple
  of the resolution).
* The independent policy walks consecutive ELIGIBLE dates, so with weekends
  excluded it can straddle a weekend where calendar-consecutive continuous
  cannot: 8 candidates against 6 on the 3-workday weekdays-only cases.

The harness treats this as decisive: a differing candidate space, or a winner
the other arm cannot express, can never be reported as low risk, however well
the shared subset agrees. **v2 makes it a category rather than a field** — five
buckets, not four — because a case counted among the "measured" carries an
implication its comparison cannot support. The pairing verdict is a contract
fact, so it is recorded on every case and summarised cross-cuttingly: the
differing-space finding survives an environment where every case is blocked on
demand. Raising `_CONTINUOUS_MAX_WORKDAYS` would widen the comparison's range
without making the arms search the same space —
`docs/plans/CONTINUOUS_CLOSURE_CEILING_2026-08-11.md` records what that change
would actually cost, and it is not made here.

#### libsumo preflight (`tools/preflight_libsumo.py`) — PR G, BLOCKED

PR G's in-process SUMO path is blocked, and not for the reason previously
recorded. eclipse-sumo 1.27.1 IS installed; it ships the libsumo C++ library
(`lib64/libsumocpp.so` on Linux and `lib/libsumocpp.dylib` on Darwin), every
libsumo header, and no Python binding at all.
Reinstalling the same wheel cannot help. The preflight separates the three
faults that all surface as `ModuleNotFoundError` — a module absent only because
SUMO's `tools` directory is off `sys.path` (true here for `sumolib` and
`traci`), a distribution without the binding, and SUMO genuinely absent. It
checks packaged `<SUMO_HOME>/bin` binaries as well as `PATH` and recognises
Linux, Darwin and Windows library suffixes. It installs nothing. The subprocess backend stays the only execution path, so the
approved seed-worker budget and every process and memory cap stand as
benchmarked.

### F — Confidence (`validate_sim.py`) — CORE BUILT
LOSO results (2026-07-05, whole day): the program recovers a median 32 %
of a hidden station's traffic (range 0.06–0.83). CONFOUND WARNING (missing
from this section until 2026-07-09 — an internal inconsistency with
section C's own caveat, which this exact figure is the subject of): this
0.32 was measured on the SMALL TWO-CLUSTER network, before the city-scale
expansion, corridor coupling, and the PFE shape-pool fix — it is NOT
comparable to section C's 0.09/0.093/0.154 figures (all on today's full
inner-city network, and themselves now also pending re-validation against
E-I/I-E + hour/day-type purposes). Kept here for the station-level pattern
it illustrates (which sensors are structurally easy/hard to infer), not as
a current headline number. The spread IS the
observability story quantified: 133 recovers 0.83 (its twin 2276 measures
the same street across the junction), isolated 1074 recovers 0.06 (nothing
infers it). Two consequences, both by design: (1) unmeasured streets carry
LOW confidence unless redundantly sensed — the map must say so; (2) the
parsimony bias (LP under-fills unconstrained streets) is measured, and the
constructive levers are the gravity OD prior and real network-wide level
data (city catalogue ÅMVD / FCD). Learned volume-LEVEL priors were tested
and REJECTED: leave-city-out error factor 4.5–14× (volume_priors.py kept
as the documented negative study) — levels do not transfer across cities,
shares do.
Per-edge confidence = f(provenance level, leave-one-station-out error curve,
Monte Carlo spread, B's interval width). The 107 direction split (city-
measured 52/48) is a standing validation anchor for "can the program infer
an unmeasured direction". Confidence doubles as a **sensor-placement
guide**: the map shows where the next station buys the most certainty.

## Invariants
One ID space (`u_v_k` everywhere) · WGS84 on all interfaces · absolute time
(epoch + 15-min index, ISO-T, UTC parse) · directions are metadata, never
guesses · missing ≠ zero · every number carries provenance + confidence.

### Monthly warm-state activation (2026-08-03)

The recurring monthly command now enables the existing mesoscopic warm path by
default. `MonthlyDemandResolverRunner` forwards one explicit
`WarmPrefixController` to every archived-demand child; `--cold-execution`
retains a deliberate all-cold mode. Warm execution is limited to one seed
worker because the current TraCI controller owns one active connection.

Warm cache entries remain content-addressed by exact demand variant, seed,
network, route mutation, warm point, SUMO/platform identity, snapshot settings
and every state-interpreting source. Restoration revalidates the state,
prefix-evidence and passing equivalence certificate. On a cache miss, the first
observation bootstraps a provisional state from zero; later compatible
observations extend the nearest earlier provisional state and transport exact
prefix evidence rather than replaying from zero. Provisional states are not
published; malformed evidence, bootstrap or extension failure records the
reason and executes the unchanged cold observation. The v16 paired campaign
(`53ea67be…36ef0`) passed exact semantic
equality for q10/q50/q90 and supplied the three atomically adopted product-cache
entries. A true cache-hit run completed all three in 71.568 s versus 88.506 s
cold (19.1% faster); bootstrap time is intentionally excluded from that claim.

### Annual warm-state population boundary (2026-08-03)

Annual coverage is a separate candidate-free bank, not an expansion of the
product cache's trust. `annual_warm_plan.py` projects the complete independent
daily 2027 timing domain: every 15-minute-aligned interval within 00:00–24:00.
Of 1,699,440 possible intervals, 1,682,634 have an exact source-year/DST
envelope. They collapse to 34,895 road-independent checkpoint requests and
104,685 exact q10/q50/q90 seed states. `AnnualWarmPlanIndex` maps schedules for
different directed edges to those requests, but its result is only a requested
checkpoint: the existing route-mutation audit, safe-boundary calculation,
exact identity, equivalence certificate and cold fallback remain mandatory
before product reuse.

`annual_warm_store.py` keeps a state together with its exact route, prefix
evidence and demand provenance. This is required because SUMO save-states omit
future departures. Members are deduplicated by their original SHA-256 and
deterministically gzip-compressed only when smaller; restore verifies both the
stored representation and original bytes. Artifact validation also cross-binds
the selected variant route, demand metadata/spec and demand manifest bytes to
the hashes in the named immutable demand archive; archive labels alone are not
accepted. Unit manifests and content blobs publish without replacing an
existing immutable identity.

`populate_annual_warming.py` orders every demand-build/seed/variant group as an
exact predecessor chain. Its first checkpoint bootstraps from zero and every
later checkpoint must validate and extend the preceding artifact; a missing or
inexact predecessor stops the chain rather than silently replaying from zero.
It also owns a transactional SQLite progress registry and
three persistent spawn-isolated worker processes, so each TraCI controller has
a private process/connection without paying interpreter and plan-validation
startup for every state. Existing progress databases are accepted only after
every immutable unit row, lifecycle field and SQLite integrity result match the
plan. A crash after artifact publication but before the progress commit is
reconciled by validating the orphan artifact instead of rerunning SUMO. Root,
plan, progress, store, staging and population-lock types fail closed on symlink
or non-regular substitutions.

The execution hot path validates and indexes the full plan once per process,
retains one immutable archived-demand runner per worker/current demand build,
and passes the parent's already verified archive record into isolated workers.
Predecessor restore reads and hashes only the state and prefix evidence consumed
by chaining; shared route/demand members remain bound by the immutable manifest
and current archive validation. Progress transitions finish one dependency-
ready batch atomically. Crash-published orphans receive the stronger full
semantic restore check before success: prefix schema and warm point, demand
contract, state mode/time/version and all member hashes.

Each extension materializes only the departure-sorted route window for its
exact predecessor/checkpoint interval. Loading the full three-day route at every
link caused parsed route definitions to accumulate in saved states; a real
96-link pilot exposed state growth to 45 MiB before this was fixed. Route-window
states now remain 1.24–1.59 MiB. The mesoscopic tripinfo accumulator omitted by
SUMO state serialization is re-quantized to its native millisecond `SUMOTime`
grid at each internal handoff and rounded to production precision only for the
final metric. A maximum-depth regression and independent cold bootstraps at
links 2, 48 and 96 require exact vehicle populations, values, completed order,
insertions, teleports, queues and recovery buckets. The only permitted audit
difference is SUMO's non-behavioural `loaded` route-parser lookahead; it remains
relationship-checked and may not excuse any other counter mismatch.

The 367 canonical demand builds normally cover previous/current/next day and
are shared by all compatible windows; exact shorter fallbacks handle source-year
and DST boundaries. Annual, monthly and direct demand builds share one reentrant
inter-process lock across live-release snapshot/build/restore, so concurrent
calibrations cannot restore stale bytes over another build. The plan binds the
SUMO/platform identity and every state-producing source. Initial population
requires 160 GiB free and execution stops before dropping below an 8 GiB
reserve; these gates replace the unsafe original 8 GiB readiness threshold.

Cache identity does not include the repository commit hash: exact source-file,
input, runtime, platform, SUMO and serialization fingerprints are the effective
identity, so unrelated commits do not invalidate states.

Final plan `9cc823d3…45283b` binds the every-edge demand release, route-window
chaining, millisecond accumulator transport and the corrected disk gate. The
real 96-link q10 pilot under the immediately preceding state-equivalent plan
completed with zero failures; its v2 audit restored every link and passed all
selected behavioural cold comparisons. The only later bound-code change is the
preflight constant described below, so none of those diagnostic artifacts is
eligible for production reuse or relabelling.

A measured three-day archive occupies 326 MiB and the q10 96-link store occupies
40 MiB. Projecting 367 retained archives plus three variant chains, staging and
the 8-GiB runtime reserve showed the former 160-GiB gate could admit a run with
effectively no completion margin. The initial minimum is now 192 GiB
(206,158,430,208 bytes). Current free space is 180,475,920,384 bytes, so the
final preflight fails closed and no production root is initialized. Full
population has not started. Population does not activate or certify the bank
for product use; 16,806 intervals whose envelope cannot be represented exactly
remain unavailable and therefore fall back cold.

### Independent-day long closure search (2026-08-03)

Long calendar searches now have an explicit
`independent_daily_reset_v1` policy. It is an opt-in modelling assumption, not
a reinterpretation of the continuous contract above. Each candidate is split
into exact road/date/start/end daily SUMO units; date-specific forecast demand,
q10/q50/q90 variants, canonical seeds, the full production six-hour recovery
cap and all normal feasibility/health metrics remain unchanged. Only traffic
state between work days is reset. Parent evidence is reconstructed by summing
matched baseline/candidate pairs for the same variant and seed before the
existing robust decision.

`exact_balanced_daily_v1` allocates the requested work in exact 15-minute
blocks. For example, 50 hours over nine days becomes two 5 h 45 min days and
seven 5 h 30 min days; all legal placements are enumerated and overshoot is
zero. Its sequence uses consecutive eligible work dates, so deselected
weekends and explicit blackout dates are skipped rather than making an
eight-to-ten-workday schedule impossible. The exact selected dates are bound
into the schedule ID. That balanced policy remains available to explicit
non-rolling internal workflows. The web rolling-period tool instead always
uses `exact_equal_daily_v1`: requested work must divide into identical
15-minute-aligned daily shifts, so every selected workday has the same start
and end time. `permitted_daily_band` is a containing search window, not the
chosen closure itself: a 07:30–15:15 candidate is legal inside 06:00–18:00.
This also bounds allocation growth. Independent-day rolling
searches are capped at 90 workdays; continuous searches retain the validated
21-day ceiling.

`rolling_period_v1` is the opt-in long-range comparison contract. It does not
mean calendar week: every legal schedule is a rolling period and may cross
week, month, or year boundaries. The result keeps one compact best viable
schedule per start date, with its exact end date and workday count, while the
workspace retains the full schedule/evidence ledger. The browser can therefore
search across several months and compare periods from a few days through 90
workdays without forcing them into an ISO week. Ranking uses provisional v2's
same `closure_cost_v1` pilot/final objective; analysis is UI-visible but cannot
open the global-best release claim while that policy remains provisional.

Daily evidence is content-addressed by simulation-affecting unit and child
backend identity, not by parent search ID, month range or total-work label.
Consequently, widening a search can reuse already-computed exact units. Daily
units execute in isolated interpreters, each with a private TraCI connection;
seed-level and daily-level parallelism cannot be multiplied.

HOW UNITS ARE FED TO THAT WIDTH (2026-08-27). Batching used to be PARENT-LOCAL:
`run_candidate` collected one parent's pending units and sized the pool
`min(unit_workers, len(requests))`. A five-day parent supplies at most five
units, and once the cache is warm it supplies about one, so an operator asking
for eight workers measurably got one. The live campaign
`ui-monthly-13lhsoy-5d` ran at 80 330.94 worker-seconds over 88 771.27 active
seconds - a ratio of 0.905, i.e. one busy worker against eight configured slots
(11.3%) - with 20/20 process samples showing a single worker, while its cache
counters read 3 229 hits against 851 misses over 816 parents, i.e. 1.04
genuinely new units per parent. The configured width was never the binding
constraint; the supply of independent work was.

`GlobalDailyUnitQueue` (opt-in via `TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_WORKERS`,
width normally `--daily-workers`) enumerates the missing units ONCE across the
whole shortlist in canonical unit order and serves them from exactly `workers`
puller threads. A parent promotes
its own units and waits only for those; the rest of the width runs lookahead
into the same content-addressed cache. The contract, all test-pinned:

* the thread count IS the SUMO ceiling and the backpressure - one unit per
  thread at a time, so outstanding work never exceeds the width;
* every content key is single-flight across processes via `flock`, the cache
  is RE-READ and re-validated after the lock is taken, and publication is
  atomic and last, so a lost race costs a read and an interrupted unit
  publishes nothing;
* a parent assembles its result from the cache in its own canonical unit
  order, so completion order cannot reach the evidence;
* coverage participates in the work identity, so a finalist round asking for
  more repetitions rebuilds the queue instead of returning pilot coverage;
* retiring a queue never holds `_state_lock`, whose only other holders are the
  puller threads themselves - the obvious ordering deadlocks on the first
  finalist retarget and is pinned by a regression test.

WHERE THE SWITCH LIVES, AND WHY IT IS NOT A CLI FLAG. `monthly_sumo.py` hashes
NINETEEN source files into `source_digest`, and `run_monthly_closure_search.py`
is one of them. That digest travels in the backend provenance the daily-unit
cache key is built from - `_stable_backend_identity` drops only the four
search/release labels - so a single added CLI flag moves `source_digest`
(measured: c0bbfc32... -> 8b040d90...) and orphans every cached unit. The
switch therefore lives in `independent_daily.py`, which is NOT among the
nineteen: `TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_WORKERS` for the width, plus a
required `TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_SCREENING=independent-exhaustive`.
The second variable is a gate, not decoration. Global lookahead produces units
nobody has asked for; under `--screening-mode=independent-cost-ordered-exact`
that would simulate precisely the work the mode's stop proof claims to have
skipped, making its recorded saving false. The resolver fails closed on a
missing, unknown, or command-line-contradicting declaration.

EVIDENCE, WITH ITS KIND STATED. SYNTHETIC SCHEDULER SCALING, 180-unit fixture
replaying one seeded cost profile per arm with a sleeping stand-in in place of
SUMO: achieved width rose 0.999 -> 7.771 (7.78x, 97.1% of theoretical) with
byte-identical cache across arms. This measures the scheduler, not per-unit
SUMO cost. A SAVED REAL cold SUMO observation (170 samples, not repeated
since) reached exactly 8 concurrent isolated workers and 8 concurrent SUMO
processes without exceeding either. The campaign ETAs of 2.93 h (resume) and
6.58 h (cold) are PROJECTIONS multiplying production's 94.396 s/unit by the
measured width; no full campaign has been run at either width. Per-unit cost
under sustained eight-way contention is NOT measured, and the report states
that more than ~21% per-unit inflation would put the eight-hour goal at risk.
Broad cache-hit reconstruction does not publish one redundant pilot file or
progress-manifest rewrite per parent. The full pilot statistics and schedule
ledger remain workspace artifacts, while the final API payload contains only
the bounded finalists/selected schedules.
Without an adopted proxy gate, the server uses
`independent_daily_exhaustive_sumo_v1`: every available parent is retained,
unavailable parents are reported, and each unique daily unit runs once. This
is exact within the stated independent-day
model and UI-visible under restricted claim wording, but the first uncached
road/range is not an instant operation. Cache hits and completed resumable
work are the near-instant path. The concrete 50-hour, 15:00–22:00 contract
with Monday–Friday work dates enumerates January–May as 22,666 parent schedules
backed by only 2,675 unique daily units; the full 2027 calendar is 57,932
parents / 6,525 units. On the current machine those process-free enumerations
completed in about 4 s and 10 s respectively; these are planning times, not
SUMO runtime claims. For the full-year weekday example, 25 late-31-December
daily units (affecting 229 parent schedules) are explicitly unavailable
because their six-hour recovery would require nonexistent 2028 forecast
demand; the remaining 57,703 parents / 6,500 units continue normally. A result
with any unavailable schedule is labelled best only among the available
schedules and cannot make a global-best claim.

Independent-day cold execution also respects the reset boundary operationally:
when a reusable archive extends beyond a unit's declared envelope, SUMO starts
at that envelope's midnight and ends at its recovery boundary with no post-end
flush. The exact `(begin, end, interval count, flush)` window is part of the
matched-baseline cache key. Continuous runs retain archive-start execution so
overnight vehicle carryover is not truncated. A SUMO timeout or failed process
is recorded as a candidate-local hard failure (`sumo_execution_failure:*`)
instead of aborting the whole search; the evidence and claim gates remain
fail-closed.

The obsolete v1 annual readiness record covers only 07:00–15:30 and is retained
as historical pilot evidence. Earlier full-day v2 roots predate the pre-run
hardening and are also superseded. The current v3 plan is the full-day contract
described above. Full population and any later product-cache activation remain
separate work; the unchanged cold path is authoritative on every miss.

### Transactional paging for large closure calendars (2026-08-11)

Independent-day enumeration now has two separate resource concepts. The server
policy permits at most 30,000 new unique daily units in one invocation, while a
100,000-unit cumulative ceiling and the existing 100,000-parent ceiling remain
fatal safety limits for the complete search. The per-invocation count resets on
resume; cumulative identity and classification do not.

A parent schedule is the transaction boundary. All of its unit identities and
envelope checks are completed before any parent, unit or eligibility state is
committed. If the next parent would cross the page budget, it contributes
nothing to the checkpoint and is reconsidered on the next invocation. The
checkpoint binds the search, budget, exact evaluated-unit prefix, exact
classified-parent prefix and cursor. Tampering, a missing cursor or a changed
budget fails closed.

Paused enumeration is published as `monthly_screening_checkpoint`, never as
`monthly_proxy_screening`, and contains no shortlist. `run_monthly_search`
returns before backend preparation, provenance publication, SUMO, pilot or
finalist work. Repeating the same immutable `ClosureSearchSpec` consumes the
latest checkpoint; after the final page, the resulting screening payload is
byte-identical to an uninterrupted enumeration. The web API exposes the
versioned resource policy and a `paused` state, and the browser restores the
exact form and directed edges after reload.

Resource-policy v2 also declares execution width rather than inheriting CLI
defaults: independent-day jobs started by the web server pass eight daily
workers, one seed worker and an eight-process active-SUMO ceiling. The CLI
still validates that width against the recorded isolated-worker resource gate;
the policy is an admitted resource setting, not a linear speed claim.

Every search workspace accumulates `active_elapsed_s` across resumed process
segments with an awake monotonic clock (`CLOCK_UPTIME_RAW` on macOS), while UTC
creation/finish timestamps retain ordinary wall-clock provenance. Throughput
evidence must use the active value: wall elapsed may include laptop suspension.
While the monthly CLI owns the shared simulation workspace on macOS it starts
`caffeinate -i -w <pid>` and releases that assertion during cleanup; failure to
start it is visible on stderr rather than silently pretending sleep prevention.

The named six-month 360-hour case contains 11,813 parents and 23,349 unique
daily units. It is therefore admitted by the current server policy instead of
being rejected by the historical 10,000-unit implementation cap. This changes
scalability only: independent daily reset, fixed equal daily start/end times,
the provisional policy boundary and all release/global-best gates remain
unchanged.

The parent ceiling has one authoritative value when paging is enabled: the
CLI cap and `DailyUnitBudget.maximum_parent_schedules` must match or preflight
fails before enumeration. A unit budget is rejected for proxy and bounded
screening modes, where it cannot be consumed. Checkpoints intentionally use a
separate schema from completed screening; normal execution fields are absent
because no result consumer may enter the result path until resume completes.

### Warm/cold window evidence guard (2026-08-13)

The v16 warm-state campaign compared warm execution against the former
full-archive cold arm. Commit `adf765b` later shortened independent-day cold
runs to their exact envelope. A production observation now permits the warm
arm only when its candidate's cold window is still byte-for-byte the full
window covered by v16. If the windows differ, it records
`warm_cold_window_equivalence_unproven` and executes the trimmed cold arm.
This prevents historical warm evidence from authorizing a different reference
horizon; trimmed warm execution requires a new paired equivalence campaign.
Because the guard changes a cost-interpreting source, the process-free golden
was re-frozen as `closure_cost_ordering_golden_v4.json`; v1-v3 remain
immutable history.

### Monthly API workspace-lock handoff (2026-08-13)

The web server's simulation slot has two layers: a thread slot serializes API
jobs and `runs/.demand-workspace.lock` serializes writers across processes. A
real multi-month execution exposed that the server retained both layers while
waiting for `run_monthly_closure_search.py`; the CLI then waited for the same
cross-process lock held by its parent. The monthly launch now retains the
thread slot but releases the workspace flock immediately before spawning the
CLI, which acquires and owns it for the complete run. Other API simulations
remain blocked and an external writer that wins the handoff race merely makes
the CLI wait; no two workspace writers proceed together.

`monthly_multimonth_e2e_outcome_v1.json` records the rerun: two exact dates in
different months reached backend preparation, pilot, finalists, five decision
rounds and publication. Both finalists later timed out at the unchanged 300 s
limit, so the honest terminal result is `no_viable` and no best claim opens.

### Cost-order multi-case benchmark v5 (2026-08-13)

The single-case benchmark could repeatedly select the already observed March
case, and its archive-derived search id abbreviated a directed edge to its
upstream junction. Distinct edges could consequently share one workspace id.
Discovery now includes a digest of the complete directed edge in the search
id. The v5 harness freezes four outcome-blind cases round-robin across
2027-03-22 and 2027-07-15, requiring four distinct edges, and applies the
unchanged per-case semantic gates plus aggregate health and positive-saving
gates. Its registration was committed before any v5 outcome was produced.

V5 saved 18 candidate verifications in total and found three cases with at
least two health-viable pilot candidates, but failed strict equivalence. One
case preserved winner, selected ids, health, hard failures and restart while
saving 11/13 verifications, but four exhaustive timeout observations lacked
the deterministic post-SUMO fields present in the pre-SUMO ledger. Another
strictly field-identical case saved 0/13. In the remaining two cases the same
candidate/seed crossed the unchanged 300 s wall-clock boundary in one arm but
not the other, changing hard-failure, health, selected-id and restart evidence.
All four stop proofs were independently valid. This is a fail-closed
reproducibility result: policy v3 is not activated and held-out/micro release
evidence does not run downstream of it.

### Exact interactive closure cache (2026-08-21)

`serve.py` keeps exact structured closure reuse in `runs/scenario-cache/`,
separate from the published scenario directory. A schema-v2 cache identity
hashes the canonical `ScenarioSpec`, execution/output policy, Python/platform/
SUMO runtime, direct route and agent bytes, network/metadata/flow inputs and
the relevant scenario source tree. After a normal run has passed the existing
`verified_clean` gate, the server writes an atomic sidecar naming the scenario
and trajectory. A later structured request can return immediately only when
the sidecar provenance/digest matches, the live index still contains the same
normalized spec, both safe in-directory output files exist, and the payload
remains `verified_clean`. Verification holds the normal cross-process
simulation workspace slot, repeats the identity check against source edits,
and a miss is cached only if the pre-run and post-run identities agree.
Identical concurrent misses share the first job; malformed cache data is a
miss. Loose legacy query requests are not cached because they do not carry
enough identity to prove an exact repeat. Cache failure is fail-open to the
normal SUMO path; a hit is explicitly labelled cached and does not claim a new
start time. The whole post-acquire preparation region has one final release
guard, including unexpected cache-identity failures, so an exception cannot
strand either the process-local slot or the cross-process workspace lock.
While locked verification is in progress, the close-status API uses the
distinct `checking_cache` state and cancel may terminate preparation before a
child exists; only a miss changes atomically to a durable `running` job and
receives a new `started_at` timestamp. Cache payload fields are allowlisted so
an index entry cannot overwrite lifecycle status or the requested spec.

The same speed work keeps first-run experiments opt-in: `run_scenario.py`
accepts `--rerouting-threads` and `--routing-algorithm` only as explicit
benchmark parameters; they require an isolated output directory plus timing
sidecar and refuse the live scenario directory. EdgeData's production default
is the paired-qualified `entered timeLoss` field set; `--full-edgedata` is an
isolated rollback/benchmark arm. Phase-profile schema v2 reports deterministic
disruption, payload construction and atomic artifact publication separately.
`traffic_sim/simulation/disruption.py` uses batched sparse shortest paths for
larger OD sets, retains a grouped Python fallback and keeps the former per-OD
calculation as an equivalence oracle. Seed workers receive a frozen
`SeedRunPlan`, and final scenario/index publication cannot launch SUMO.
Monthly
independent-day runs expose result-neutral cache/worker timing in their
resumable progress detail and enforce a declared
`--max-active-sumo-slots` budget before expensive work starts. Nested daily
and seed parallelism remains refused until its full equivalence/resource gate
passes. The matched-baseline cache now has per-key cross-process single-flight;
one producer computes under `flock` and every waiter re-reads and verifies the
atomic result. Waiters emit one content-key diagnostic and fail after a bounded
600 seconds by default rather than appearing dead forever; lock pathnames are
retained because unlinking an active `flock` pathname can split serialization
across inodes. Browser job polling uses bounded exponential backoff and stops
after five consecutive transport/HTTP failures with an explicit reconnect
message; the server-side job may continue and can be recovered by status.
Neither the
routing matrix nor a worker allocation is a production adoption until its
paired semantic/evidence and resource gates pass.

### Sub-hour evidence execution boundary (2026-08-31)

The Phase 3 paired benchmark and Phase 4 cold-ledger profile are serialized
writers of one shared demand workspace. The staged-evidence ai-flow protocol
now enforces the boundary instead of relying only on a repair prompt. Mutable
code stages and deterministic code checks cannot create or alter configured
evidence artifacts. Deterministic checks and a
complete independent code-stability review must approve first; the controller
then persists a content-hashed `code-freeze-*.json`. During evidence execution
that manifest and the SHA-256-bound per-series artifact budget are checked at
launch, every progress heartbeat, completion, after final checks and during
final review. Source drift, evidence overwrite or deletion terminates the
owned process tree. The kernel `flock`, queried through
`workspace_holder()`, is authoritative; JSON left in the lock pathname is
diagnostic metadata and is not evidence that a process is alive.

Phase 3 revalidates the complete registration immediately before and after
every case. Source drift stops later starters and yields
`INCONCLUSIVE_SOURCE_DRIFT`; completed case work remains isolated and is named
only by unpromoted digests. Registrations and outcomes use atomic no-clobber
publication, so an interrupted or competing writer cannot replace an existing
evidence ID. Phase 4 captures its spec, policy, runtime and complete producer
source manifest before preparation, revalidates it after measurement and once
more immediately before atomic append-only publication. Thus a long
measurement cannot be attributed to source bytes installed near its end.

The ai-flow controller starts model invocations in their own session and also
tracks descendant process groups created by tool commands. Interrupt, timeout,
or a nominal agent exit reaps any tracked descendant groups before the stage
can continue. This closes the gap where stopping a model left a benchmark in a
separate session holding the shared workspace. A `BLOCKED` response from a
worker, test fixer or review fixer receives a bounded live-state recheck; the
scientific terminal states remain valid results and do not become workflow
blockers. Gate S is published only from a complete terminal population bound
to the same source lineage.

The staged controller also separates the expensive evidence attempt at the
Phase 3--5 boundary. Its first source-frozen evidence invocation may append
only the configured bounded-SUMO, cold-ledger and conditional-index series.
The controller then runs source-frozen deterministic checks and asks an
independent read-only reviewer to inspect those exact bytes. It persists
`phase-3-5-checkpoint-*.json` and a content/lineage-bound
`phase-3-5-review-*.json` with `status: PASS`; a reviewer response alone is
not sufficient. Any newly appended full-month or Gate S series before that
approval fails closed. A separate post-review evidence invocation is the only
stage allowed to register Phases 6--7, and it must revalidate the checkpoint
lineage before starting. This keeps a model's one invocation from silently
crossing the Phase 3--5 review gate.

The expensive workflow has at most one all-findings code repair followed by
one reserved verification review, at most two evidence generations and at
most two new artifacts per configured series relative
to the run's initial inventory. A final-review source repair consumes the one
allowed refreeze/rerun. Interrupted evidence retains its in-progress generation
number on resume, so retry does not manufacture a new evidence version. Legacy
runs without the v3 initial inventory and review-budget checkpoint cannot be
upgraded in place. A verification review that still requires changes stops
before evidence and never launches an unreviewable fixer.
The detailed rationale and primary sources are recorded in
`docs/plans/AI_FLOW_STAGED_EVIDENCE_PROTOCOL_2026-08-31.md`.

## Build order
1. **B — observability module** (junction solves, bounds, alarms).
2. **C — PFE-lite LP** (replaces routeSampler as primary; keeps its I/O).
3. **D→C wiring** (`--source forecast --date …`) — "close a street next year".
4. **F — leave-one-station-out + provenance surfacing.**
5. data_in/sensors.json metadata file (A's last hard-coding removed).

## Parked (validated studies, not on the critical path)
`dirsplit/` transfer model — now level 3's engine for street/time priors. Its
central profile was re-chosen in 2026-08 by a leakage-free tournament
(`dirsplit/benchmark.py`): the per-sensor LightGBM quantile models lost to a
flat 50/50 leave-city-out and were removed together with the Gaussian
`estimate_directions.py` fallback and the Norwegian acquisition client; the
deployed model is an hour × day-type D-factor pooled toward 50/50, re-levelled
at load time by the city's published local D-factor where one exists (sensor
107's 52/48 for 2025). `build_dataset.py` (GNN prep — revisit only at high
sensor density).

## Key references
- Bell & Shield (1996); Chen et al. (2009); Yang & Sun — **Path Flow
  Estimator**: single-level convex estimation from partial counts.
- Castillo et al. (2015), *J. Sensors* — link-flow observability.
- Cascetta (1984) GLS; Van Zuylen & Willumsen (1980) entropy; Marzano et
  al. — identifiability: an informative prior is required.
- Cascetta et al. (2013) — quasi-dynamic OD structure for 15-min windows.
- DynaMIT / DYNASMART-X — the estimate-then-predict system archetype.
- TRR 2025 probe-volume estimation; arXiv 2605.09891 — FCD fusion upgrade
  path for level 3.
