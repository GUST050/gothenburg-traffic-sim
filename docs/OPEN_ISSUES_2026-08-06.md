# Open issues — handoff, 2026-08-06

Everything known to be wrong, unresolved, or unproven, with where the evidence
lives. Written at the end of a long session so the next one does not re-derive
it. Ordered by what blocks what, not by severity.

Nothing here is speculation: each item says whether it is MEASURED, DOCUMENTED
(carried from an earlier review), or OPEN (suspected, not established).

---

## 1. BLOCKING — LOSO cannot compare pipeline versions

**MEASURED** · `validation/loso_draw_variance_v1.json`

Three demand builds under identical code, differing only in `--seed`, gave LOSO
medians **1.537 / 1.119 / 1.490** — a spread of **0.418**, which grew to
**0.608** over six draws (SD 0.236). The shift previously
attributed to stage B was 0.971 → 1.537, a difference of **0.566**. Same order
of magnitude.

So a single LOSO run cannot separate a pipeline change from the randomness of
one candidate-pool draw.

**Consequence, and it is retroactive:** every single-draw LOSO comparison in
this project's history is weaker than it was presented as. Any future claim of
the form "change X improved/degraded recovery" needs several draws per version
and a comparison of distributions.

Design numbers, computed: with n=3 per arm the smallest possible two-sided
Mann-Whitney p is **0.100** — such a comparison can never reach significance.
n=4 is the minimum (p=0.029); the observed effect is 2.47 SD, so **6 per arm is
comfortable**.

## 2. RESOLVED IN PRACTICE — stage B shows no effect on held-out recovery

**MEASURED** · six draws of stage-B code:

```
0.929  1.119  1.384  1.401  1.490  1.537
median 1.393   range 0.608   SD 0.236
```

The pre-B baseline is **0.971**, which falls INSIDE that range, near its low
end. The alarm was raised by comparing a low draw of one distribution against a
high draw of the same one.

So there is no evidence stage B degraded recovery, and the concern that it
should not be warmed on is withdrawn.

**Still not a formal test:** the pre-B arm remains n=1, so this shows the
baseline is unremarkable under the stage-B distribution rather than proving the
two distributions match. Six pre-B draws (`6e5763e^`) would settle it properly.
The trap if anyone does: a git worktree has none of the gitignored inputs, so at
minimum `sumo/net.net.xml` must be copied in and the build verified before six
runs are spent on it. Low priority now.

## 3. BLOCKING WARMING — the annual plan key is stale again

**MEASURED**

Every change today touched bound sources. `tools/plan_annual_warming.py --write`
must run before any population run, and the run must not start until the
`run_scenario.py`/`metrics.py` work is finished — a bound-source edit mid-run
discards the units already built, not just the run.

Full constraint table in `docs/plans/WARMING_PLAN_2026-08-05.md`.

**Still true after the 2026-08-06 fixes, and for the same reason:** three of
the four fixes landed in bound sources (`traffic_sim/demand/pfe.py`,
`build_candidates.py`, `tools/populate_annual_warming.py`), so the key moved
again. Do not copy a key into a document — four different ones appear across
`TASKS.md`, `WARMING_PLAN`, the stored preflight and reality, and none of the
first three validates. Compute it when you need it.

## 3b. NOT A BLOCKER — the recorded 192-GiB disk gate never existed

**MEASURED, 2026-08-06.** `TASKS.md` carried
`BLOCKED_ON_192_GIB_DISK_PREFLIGHT` as the active status. There is no 192 GiB
constant in the tree. `required_free_bytes()`
(`tools/populate_annual_warming.py:115`) derives the requirement from the work
an invocation can select:

```
104,685 x 432 KiB + 2 x 326 MiB + 4 GiB + 8 GiB  =  ~55.8 GiB
```

The stored preflight agrees (`minimum_free_bytes = 59,877,867,520`), and 172
GiB were free. The flat whole-year threshold this superseded was retired when
archives began being pruned as their chains complete. **Warming was never
blocked on disk.** Corrected in `TASKS.md` and `WARMING_PLAN`.

## 4. v9 held-out campaign: failed one check of seven

**MEASURED** · `runs/closure-proxy-validation/43e040ca…/report.json`

```
practical_winner_recall            1.0    PASS
discriminating_practical_winner    1.0    PASS
p90_normalized_shortlist_regret    0.0    PASS
failure_disqualification_recall    0.79   PASS
discriminating_case_fraction       0.5    PASS
all_shortlists_contain_eligible    true   PASS
ranking_case_fraction              0.4    FAIL  (floor 0.5)
median_spearman                   +0.945        (v3-era was -0.976)
```

Three of five cases had zero eligible schedules. The cause was C1 (below), now
fixed — so **a v10 on the same selection rule should pass**, and that is the
shortest path to a passing campaign on the deployed objective.
`global_best_claim_allowed` stays `False` until it does.

## 5. Closure integrity

**C1 — FIXED** (`ecdbe57`). Denied departures and shortened trips were hard
disqualifiers, which made every edge trips depart from permanently uncloseable.
Now reported as impact; artifacts (teleports, closed-edge throughput) still
disqualify. Fixing it exposed a hole it had masked: `closure_disruption` priced
a denied departure as a *free* detour, so a closure denying 85 departures could
have ranked best. Denied departures now count as `vehicles_no_detour`.

**C2 — REVISED** · `validation/closure_leak_mechanism_v1.json`. The leak is a
teleport *outcome*, not the teleport itself. Teleports are necessary (0 of 35
throughput cases lacked one) but not sufficient. A directly observed teleport
was *refused* entry to the closed edge and landed past it.

**C3 — MEASURED, NO CHANGE** · `validation/rerouter_radius_sweep_v1.json`.
400 m → 3000 m grows the rerouter 86× with no wall-time cost, and teleports stay
at 1 at every radius. Both the cost premise and the benefit premise are refuted.
Limitation: one teleport on one edge is a thin sample.

**OPEN — 21 of 61 disqualified v9 schedules** carried no teleports and no
throughput. They failed on unreachability alone. C1 should have addressed this;
it has not been re-measured against a fresh campaign.

## 6. Direction split — the unmeasured carriageway

**Current state:** ceiling only, never a floor. Five edges. Binds in 24.0% of
edge-quarters.

**MEASURED — the floor was wrong.** A two-sided bound bound at the *floor* in
40.1% of edge-quarters: the solver wanted less traffic on the unmeasured side
than the model demanded, so the constraint manufactured vehicles no measurement
asked for. Removed.

**Structural cause, unfixed:** the candidate pool is sensor-anchored, so the
opposite carriageway carries 3× to 10× fewer routes (283 against 2,779 at
station 133). Meeting any floor means inflating the few routes that exist. The
established remedy is **column generation** — generate paths that can carry the
flow — not coercing the paths already there. Not built.

**OPEN — should the ceiling exist at all?** Link-flow-observability theory says
the first question is whether the opposite direction is *determinable* from the
7 measured edges by conservation. If it is, no model is needed; if it is not,
maximum entropy (leaving it free) is the principled default, and the deployed
PFE already does that. The project's own shrinkage (λ=0.256) says confidence in
the prior is minimal, which is exactly the regime where entropy wins. **The
observability question was never measured.**

**MEASURED — weekday adds nothing** · `validation/dirsplit_weekday_signal_v1.json`.
0.003 spread across Mon-Fri against a declared interval of 0.078-0.236, and
splitting would cut cell support from 20 observations to 4.

**FIXED, and the most important line of the lot:** `build_targets` multiplied
every sensor edge by a direction share. Once the model predicted both
carriageways, measured edges resolved to ~0.5 and a measured 50 would have been
calibrated as **25 — silently, at 100% GEH against the halved target.** It now
splits genuine two-way totals only.

## 6b. FIXED — the relaxation ladder bought feasibility with measured counts

**MEASURED, 2026-08-06** · 12 demand builds, 6,336 interval solves.

`solve_interval_with_relaxation`'s ladder ran (tol ×2, bounds kept) → (tol ×4,
bounds kept) → (tol ×4, bounds dropped). There was **no rung that dropped a
plausibility bound at the unwidened band at all**, so an interval infeasible
only because of a Level-2 bound paid for its feasibility with up to 4× the
measurement tolerance — the exact trade the function's own docstring says must
never happen.

```
clean             4861   76.7%
relax_tol2x         22    0.3%
relax_tol4x         19    0.3%
relax_no_bounds   1434   22.6%   <- tol x4 AND bounds off
```

The two tol-widening rungs rescued 0.6% of intervals while 22.6% took a
widened band they may never have needed.

**And GEH<5 structurally cannot see it.** `tol = max(2.0, 0.05·target)·mult`,
so ×4 permits ±max(8, 0.20·target). At the far edge of that band GEH is 2.14
at target 10, 1.91 at 100, 3.81 at 400; breaching GEH 5 needs a target above
~688 veh/quarter. **The largest count ever measured on any of the 7 measured
edges in any quarter is 203.** So "100% GEH<5" is guaranteed to pass wherever
in the relaxed band the solution lands.

**Fix:** `RUNG_NOBND_TOL1` inserted as the FIRST relaxation — bounds dropped,
band untouched. The tol-widening rungs follow and are now reached only when
dropping the plausibility layer was not by itself enough. The
`allow_structural_relaxation=False` path skips it with `continue` (was
`break`), so those callers see the pre-fix ladder exactly. The build warning
no longer says the flat "sensor constraints were retained" — it names the
widened-band interval count and multiplier when there is one.

## 6c. FIXED — the Level-3 priors outranked the measured counts

**RESOLVED 2026-08-06, later the same day.** The section below is kept because
its geometry is right and worth having; its *conclusion* was wrong, and the
correction is instructive enough to state first.

**The pool was never the problem.** The claim below — "the pool cannot span the
target vector, and no ordering of solver relaxations can change that" — is
refuted by direct measurement. An LP over the same shapes finds a nonnegative
route-flow vector inside the UNWIDENED band for **every** one of the 12
widened Saturday intervals, and for all 96 quarters on both a weekday and a
purpose-built weekend pool. The pool carries 493 routes reaching those outflow
edges *without* the measured inflow — ample freedom.

**What actually happened** was the third instance of the inversion class fixed
twice already the same day (6b bounds, 6d quotas): the **Level-3 priors were
passed to the solver at every rung and never dropped**. The node's unmeasured
opposite carriageways `96523321_26355153_0` (prior target 2.9, weight 1.000)
and `91615277_26355153_0` (target 3.0, weight 0.714) share **153** and **340**
routes respectively with the measured outflow each one starves — precisely the
routes that approach the node from an unmeasured direction. Every iteration the
prior pulled them back down, so the measured outflows settled 2-7 vehicles
below target, at a **stable** fixed point: 200 and 2000 iterations land on the
same vector, and removing the priors hits the targets **exactly** (worst
residual 0.00 of tolerance).

**Two facts kept it hidden, both now measured and pinned by tests:**

1. **`tol_mult` never enters the IPF iteration** — it is read only by
   `_check_entropy_solution`. `RUNG_NOBND_TOL1` and `RUNG_RELAX_NOBND` compute
   a **bit-identical** vector (verified: max|diff| = 0.0) and differ only in
   the ruler. A widening rung never *found* anything; it accepted what was
   already there. So "the band had to widen" was never evidence about the pool
   — which is exactly how it got read as evidence about the pool.
2. **The complete solver sat below the widening rungs.** IPF is iterative with
   no completeness guarantee, so its failure is not proof of infeasibility; the
   LP decides that, and it ran only *after* the band had already been widened.

**Fix** (`traffic_sim/demand/pfe.py`): `RUNG_NOPRIOR_TOL1` — bounds, quotas and
the Level-3 prior layer dropped, measurement band **unwidened** — inserted
after the quota rung and before any widening rung; and the existing exact-band
LP **moved above** the widening rungs, which adds no capability (the position
it left is unreachable) but makes "the band widens only when the counts are
genuinely unservable" enforceable rather than aspirational. A new
`PRIORS RELAXED` line reports it, and `warn_widened_measurement_band` now
reports a widened band **unconditionally** — previously that disclosure was
nested inside `warn_relaxed_bound_violations`, which returns early when there
are no bound violations, so the whole `relax_tol2x` rung announced nothing.

**Verified end to end** on a real 2027-05-01 forecast build, all three
direction-split variants: widened intervals **12 → 0**, `relaxation_summary`
`{clean 63, no_bounds_tol1 21, no_priors_tol1 12}`, 100% GEH<5, 0 infeasible.
Sunday 12 → 0 and Monday 1 → 0 on the same pool. The deployed 2025-09-16
weekday build never hit this (`{clean 80, no_bounds_tol1 16}`), so no rebuild
of the live demand was required.

**Ordering, measured not assumed:** dropping the priors while *keeping* the
Level-2 bounds recovers only 6 of the 12, so the new rung is a monotone
continuation rather than a reordering of the existing contract. A finer
two-dimensional ladder (bounds × priors) would rescue those 6 at a smaller
concession — a deliberate future change, not a side effect of this one.
Restricting the drop to the provably minimal interfering set (priors sharing a
route with a measured edge) removes nothing here: all 7 priors in the affected
quarters interfere, because corridor priors are adjacent to sensors by
construction.

**What the fix costs, stated plainly:** those 12 intervals per weekend day
carry no corridor or gravity-assignment pull on unmeasured edges, so their
flow there is shaped by the counts and the pool alone. That is the intended
direction of the trade — a level-3 modelled estimate yielding to a level-1
measurement — but it is a real change to those intervals, not a free win.

**Still OPEN, and unaffected by this fix:** the opposite-carriageway *prior*
pulls that carriageway up as well as down, on an edge nothing measures. It is
the same manufacturing concern that retired the direction-split floor (§6),
one level down in the hierarchy. Worth a deliberate look at whether it should
exist at all; the ladder now stops it from costing a measured count, which is
a different question from whether it is right.

---

### The original entry, kept for its geometry (conclusion superseded above)

**MEASURED 2026-08-06.** The root cause of the widened-band intervals, located
precisely, and NOT fixed.

Three of the seven measured edges meet at node 26355153:

```
IN   26842525_26355153_0                              MEASURED
     91615277_26355153_0, 96523321_26355153_0,
     165154328_26355153_0                             unmeasured
OUT  26355153_96523321_0, 26355153_91615277_0         both MEASURED
```

The two measured outflows carry about **1.9x** the measured inflow; the rest
arrives via the three unmeasured approaches. That ratio is real and stable —
historical median **1.91**, forecast median **1.84**, with the forecast range
TIGHTER than historical (p10-p90 1.50-2.13 against 1.35-2.43). **The forecast
is not anomalous and is not the problem.**

The pool is. **94% of candidate routes on the outflow edges also use the
measured inflow edge**, so the pool can only deliver outflow ≈ inflow while
the counts demand ≈1.9x. With every constraint dropped except the counts
themselves (`RUNG_NOQUOTA_TOL1`: bounds off, quotas off, band x1) those
quarters are STILL infeasible — the pool cannot span the target vector, and no
ordering of solver relaxations can change that. The band widens because it is
the only thing left to give.

**It is a WEEKEND issue, not a forecast one.** Within the same forecast
source: Monday 1 widened interval, Saturday 11, Sunday 12. Earlier notes
calling this a "forecast" problem were imprecise.

**Cost:** ~8% of weekend intervals publish against a band up to 4x the
measured tolerance. Every build still reports 100% GEH<5 and 0 infeasible, and
GEH cannot detect this at these volumes (see 6b) — the per-build warning is
what surfaces it.

> **SUPERSEDED.** The two paragraphs above and below are the wrong diagnosis.
> The pool spans the target vector fine (LP-verified, 12/12); the Level-3
> priors were holding the measured edges off target. See the top of this
> section. The "94% of candidate routes" figure is real but was read as a
> constraint when it is not one — 493 out-only routes remain, and the PFE is
> free to scale them.

**Fix direction, NOT attempted:** the pool needs routes reaching those outflow
edges via the UNMEASURED approaches to the node. That is a generation change
(how origins are drawn around that junction) and it interacts with the
sensor-anchoring rule, since a route must still cross a sensor to exist.

**Two hypotheses were tested and REFUTED** — recorded so they are not retried:

1. *Purpose quotas forcing the band open.* Refuted for this case. The
   investigation did find a real inversion and fixed it (6d), but the weekend
   counts were unchanged by it.
2. *Sparse candidate coverage in low-demand quarters.* The sparsity is real
   (relaxing quarters: median 108 candidates, as few as 2 on a given measured
   edge, against 220/18-19 in clean quarters), but a 25% uniform departure
   floor left the relaxations at 23/23/20 — **unchanged** — while costing
   **577 s -> 950 s (+65%)** per 3-day build, roughly 24 h -> 35 h on the
   annual run. `POOL_DEPARTURE_UNIFORM_FLOOR` is therefore default **0.0**;
   the mechanism is kept and documented, not paid for.

## 6d. FIXED — purpose quotas outranked the measured counts

**MEASURED 2026-08-06**, the second inversion of the same class as 6b.

`required_groups` (the exact purpose mix) stayed active at EVERY ladder rung,
so when the quotas and the measured counts were jointly infeasible the quotas
could not yield and **the counts had to**. Proven on a minimal case — measured
edge m=100 served only by an `arbete` route, quota pinning arbete at 90:

```
with the quota     band x1 INFEASIBLE, x2 serves m=90   <- count relaxed
without the quota  band x1 serves m=100                 <- count exact
ladder outcome: relax_tol2x
```

The old justification was that an inviolable quota stops the solver
"publishing a route with a fabricated purpose label". That conflated two
things: `prepare_calibration` stratifies the pool into one variable per
(geometry, purpose), so provenance is immutable regardless of which groups are
constrained. Dropping a quota only lets the published MIX drift from its RVU
prior — a level-3 behavioural prior yielding to a level-1 measurement, which
is the hierarchy working.

**Fix:** `RUNG_NOQUOTA_TOL1` — bounds and quotas dropped, measurement band
UNWIDENED — placed after the Level-2 bounds and before any widening. Verified
on live data: one 2027-04-26 interval moved from `relax_tol2x` to
`no_purpose_quota_tol1` and now serves its counts exactly. A new
`PURPOSE MIX RELAXED` log line reports it, so the fix does not trade one
silent concession for another.

## 7. Pool and picker

**DOCUMENTED** · `docs/reviews/PIPELINE_FAULT_AUDIT_2026-08-06.md` and
`docs/reviews/DEMAND_PIPELINE_REVIEW_2026-08-04.md`

- **S2** 4,137 assignment ceilings enforced per pass, **2** bind. Two exact
  remedies named (static domination, lazy constraints); neither implemented.
- **S3** 200 IPF iterations, no convergence test — but it is an *averaging*
  design (burn-in 40, then ~160 samples averaged), so no naive early exit. The
  convergence profile is **still unmeasured**; it is the one speed item that
  should not be acted on by inspection.
- **B2** 33.8% of ceiling slots collapse to the flat 5.0 floor.
- **B1** every route touching a measured edge is seeded active, so parsimony
  prunes nothing. Worth re-reading now the baseline rule removed coverage.
- **P2** per-sensor coverage spread 3.3×. **P3** assignment prior R² = −5.148.
- **P1** 57.3% of edges carry no baseline traffic. Accepted consequence of the
  baseline rule, not a defect — but the map still paints smooth `confidence`
  across edges whose real status is "not simulated".

**MEASURED, new:** median **1 distinct route per OD pair** (max 72). For most
demand there is no route choice at all, so entropy maximisation — the whole
justification for picking one solution in an underdetermined problem — is
passive there.

**FIXED 2026-08-06 — half tours were invisible.** `drop_uturn_routes`,
`drop_excessive_detours` and `validate_routed_candidates` each delete
individual `<vehicle>` legs and none of them knows a tour has two, so nothing
re-checked pairing afterwards. Measured on the 2025-09-16 pool: **1,316 of
2,695 non-through tours (48.8%) were half tours**, and they reached the
simulation — 4,682 of 21,812 published vehicles (**21.5%**; 28.7% of tour
vehicles) were a leg whose partner had been deleted, in all three variants.

The loss is DIRECTIONAL, which is what makes it a modelling issue rather than
bookkeeping: a return leg must reach an independently drawn gate *and* pass a
sensor, so it routes more circuitously and the loop/detour filters catch it
more often. Inbound legs survived ~1.8× more often than outbound ones (E-I
118 vs 63, I-E 141 vs 82) — a net **+114 legs entering the canvas and never
leaving**.

Telling detail: `_resample_reused_template_tours` already asserts "one
complete, single-purpose paired tour", but it runs on generator output, before
these filters. The invariant was checked exactly where it held and never where
it broke.

**What was NOT done, and why.** Dropping the tour atomically was implemented
first and is available as `--atomic-tours`. It is not the default: it removes
1,316 tours / **13.9% of the pool**, which takes delivered supply to 68.2%,
**under the 75% `MIN_ROUTED_CANDIDATE_FRACTION` floor** — the build fails
outright. Lowering that floor to accommodate it would be weakening a gate to
make a result pass. More importantly the pool is a *coverage support set*: the
PFE reweights route flows freely and never reads the pairing, and the pool's
own design note says its value "lies in covering distinct (entry, exit) pairs,
not in matching their frequencies". Deleting 1,316 valid distinct route shapes
to preserve an invariant nothing consumes spends exactly what the pool is for.

The default is therefore `mark_orphaned_tour_legs`: the leg stays as valid
standalone support, its record gains `tour_partner_dropped: true` so the
provenance that flows into `calibrated.agents.json` stops claiming a tour that
has one half, and every build prints the count and the per-leg directional
split. **OPEN:** the composition bias itself is now measured and visible but
not corrected. Correcting it means generating replacement tours for filtered
legs, which is a generator change, not a filter change.

**NOT a fault, checked:** exact deduplication already happens at load
(`pfe.py:1508`), and path-size logit (Ben-Akiva & Bierlaire 1999) is already
applied as the IPF seed prior (`pfe.py:126`). I recommended both before
discovering they existed — I had measured the on-disk pool, not the solver's
variable set.

## 7b. FIXED — the demand prefetch leaked a whole build per resumed group

**MEASURED by inspection, 2026-08-06** ·
`tools/populate_annual_warming.py:735-791`.

`_start_next_build(i)` submitted a demand build for group *i+1*
unconditionally. When that group turned out to be already complete, the loop
short-circuits it with `continue`, which skips **both** `_archive_record_for`
(so the future is never popped) and `_prune_demand_archive` (so the archive is
never released).

On a resumed run — the normal case, since completed units are durable — that
is a full ~332 s demand solve producing an archive nothing consumes, left
resident at 326 MiB. It also silently breaks the
`CONCURRENT_DEMAND_ARCHIVES = 2` premise `required_free_bytes` is derived from,
while `_runtime_disk_guard` only enforces a flat 8 GiB reserve.

**Fix:** never prefetch for a group with no selectable unit, and reconcile
(wait for, then prune) any prefetch whose group turns out to need nothing —
which still happens if a sibling process finishes that group's last unit in
between. Regression test asserts a resumed run issues zero demand builds.

## 7c. FIXED — the preflight recorder stamped a frozen false date

**2026-08-06** · `tools/record_annual_warm_preflight.py`.

`--write` hardcoded `"recorded_date": "2026-08-04"` and `validate_report`
required exactly that, so a record written on any other day certified itself
as having been taken on 2026-08-04. In a repository whose whole discipline is
seals refusing to certify stale evidence, a self-falsifying timestamp is the
one field that must not be frozen. It now records the real date and validates
that the field is an ISO date.

`main()` also hardcoded `state_workers=3` with the validator rejecting
anything else, so a record for more workers was unobtainable without a code
edit — even though `approved_seed_workers()` returns **8** on this host. It
now takes `--state-workers` and requires only that the benchmark approves it,
which is the rule `production_preflight()` already enforces at run time.

**Worth knowing:** nothing on the execute path reads this record at all. Its
only consumer is `tools/freeze_annual_warm_readiness.py`. `WARMING_PLAN` §3's
claim that raising the worker count "needs a new preflight record" was never
enforced; corrected there.

## 8. Test suite: 156 failures, and why they are not fixed

**MEASURED, full runs 2026-08-06, before and after the day's fixes:**

```
before   158 failed, 3727 passed, 21 skipped   (20m31s)
after    156 failed, 3745 passed, 23 skipped   (20m41s)
```

The −2 are the only two failures that were **not** seal drift, both verified
green directly afterwards: `test_scenario.py` (see the end of this section)
and `test_annual_warm_readiness.py`, which was failing because the tracked
plan differed from live sources and is fixed by regenerating the plan. The
+18 passing are the new regression tests. Every remaining failure is
frozen-contract seal drift.

**42 of 43** validation artifacts that bind source hashes have drifted from
the live tree (reproducible statically in seconds, no test run needed).
Drivers, by number of artifacts broken:

```
20  monthly_search.py      20  metrics.py      20  monthly_sumo.py
20  monthly_warm_state.py  19  closure_calendar.py   19  run_scenario.py (+13)
18  warm_state_cache.py    18  warm_state_boundary.py
```

`monthly_search.py` and `monthly_warm_state.py` are equal-largest drivers and
were not previously named. The failures cascade: sources drift →
`verify_live_inputs()` refuses → the diagnostic never runs → downstream tests
hit `FileNotFoundError`. So the count overstates the number of distinct causes.

They are the seals doing their job — refusing to certify old evidence against
changed code. Fixing them means re-freezing evidence to match code, which this
project's discipline forbids doing casually.

**The structural part, which is the real finding:** the seals are versioned and
never retired. `monthly_warm_state_manifest_v1 … v16` all still exist, all
still drift (12-20 bound sources each), with 16 matching
`test_monthly_warm_state_v*_freeze.py` modules — 157 of the 158 failures are in
freeze/seal modules. So the count measures **accumulated frozen history, not
breakage, and it grows monotonically with every campaign freeze**. v10 will add
roughly another 14. The design decision `test_heldout_v6_freeze` needs is the
whole versioned-manifest family's decision, not one test's: when is a superseded
vN seal retired?

**FIXED, the one failure that was not seal drift:**
`test_scenario.py::test_index_lists_existing_files`. `clear_stale_scenarios()`
deliberately leaves a VALID EMPTY `index.json` after a demand rebuild, but the
`needs_scenarios` guard only tested `INDEX_PATH.exists()`, so that documented
state slipped past the skip and then failed an assertion that scenarios exist.
The guard now skips on an empty manifest, and a new unconditional test asserts
the empty manifest is still structurally valid — so relaxing the guard does not
also stop anyone noticing a corrupt one.

## 9. Mistakes made this session — so they are not repeated

Recorded because each produced a confident wrong answer:

1. **Measured against the wrong demand.** Reported an edge as zero-traffic from
   `baseline.json` (1-day 2025 historical) when the campaign binds a 5-day 2027
   forecast archive.
2. **Compared numbers from different pipelines.** Called 0.971 → 1.537 a stage B
   regression when eight commits sat between the two reports.
3. **Treated elimination as proof.** Ruled out other causes and presented the
   remainder as established.
4. **Recommended what already existed.** Proposed dedup and a path-size
   correction that `pfe.py` already implements, having measured the on-disk pool
   instead of the solver's variables.
5. **Called a results-affecting change neutral.** Said deduplicating identical
   routes was "mathematically neutral"; merging duplicate columns changes the
   entropy solution, which is the point of doing it.
6. **Nearly shipped a silent halving.** The `build_targets` share bug would have
   passed every quality gate at 100% GEH.

The pattern: measuring an intermediate artifact instead of what the system
actually consumes, and reporting a conclusion at higher confidence than the
evidence carried.
