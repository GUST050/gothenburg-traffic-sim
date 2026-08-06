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
medians **1.537 / 1.119 / 1.490** — a spread of **0.418**. The shift previously
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

## 2. OPEN — did stage B change held-out recovery?

**OPEN** · the claim was withdrawn, not refuted.

0.566 is larger than the 0.418 draw spread and may be real, but it was never
established. I presented elimination of other causes as though it settled the
question; eliminating alternatives says nothing about whether what remains
exceeds noise.

**To settle it:** six draws of pre-stage-B code (`6e5763e^`) against the six
being built now. The trap: a git worktree has none of the gitignored inputs, so
at minimum `sumo/net.net.xml` must be copied in, and the build must be verified
to produce the right thing *before* six runs are spent on it.

Until then, `speed-stage-b` is merged and warming has not started on it.

## 3. BLOCKING WARMING — the annual plan key is stale again

**MEASURED**

Every change today touched bound sources. `tools/plan_annual_warming.py --write`
must run before any population run, and the run must not start until the
`run_scenario.py`/`metrics.py` work is finished — a bound-source edit mid-run
discards the units already built, not just the run.

Full constraint table in `docs/plans/WARMING_PLAN_2026-08-05.md`.

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

**NOT a fault, checked:** exact deduplication already happens at load
(`pfe.py:1508`), and path-size logit (Ben-Akiva & Bierlaire 1999) is already
applied as the IPF seed prior (`pfe.py:126`). I recommended both before
discovering they existed — I had measured the on-disk pool, not the solver's
variable set.

## 8. Test suite: 156-158 failures, and why they are not fixed

**MEASURED.** 32 of 35 frozen artifacts have drifted from the live tree.
`metrics.py` and `monthly_sumo.py` each break 20 of them; `run_scenario.py`
breaks 18. The failures cascade: sources drift → `verify_live_inputs()` refuses
→ the diagnostic never runs → downstream tests hit `FileNotFoundError`. So the
count overstates the number of distinct causes.

They are the seals doing their job — refusing to certify old evidence against
changed code. Fixing them means re-freezing evidence to match code, which this
project's discipline forbids doing casually. Down from 259 at session start.

`test_heldout_v6_freeze::test_freeze_preview_reports_drift_without_rewriting_history`
is structural and will break again on every future campaign freeze: v6 excludes
prior campaigns' edges, so freezing v7/v8/v9 changed what a v6 re-run selects.
It needs a design decision, not a patch.

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
