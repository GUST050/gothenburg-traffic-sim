# Warming code review — can it deliver fast simulations?

**Date:** 2026-08-03 · **Reviewer:** Luna High (Claude) · **Status:** findings only,
nothing fixed, nothing changed.

**Question asked:** does the new warming code give fast normal simulations, fast
road-closure simulations, and fast closure optimization?

**Short answer:** no, and mostly not for reasons that a bug fix would address.
The mechanism is well-built and carefully verified, but its *shape* limits it to
a few percent of theoretical saving on the case it was frozen against, it cannot
currently hit its cache at all, and it does not touch the interactive
close-a-road path you actually use. The problems below are ordered by how much
speed they cost, not by how alarming they look.

Every number here was measured from the repository or recomputed from the frozen
artifacts; none is estimated. Where I am inferring rather than measuring, I say
so.

---

## 1. The prefix reuses ~6% of the run it is frozen against

The saved state is taken at `warm_point_s`, and the resumed run must still
simulate everything after it.

| quantity | value |
|---|---|
| simulated span | 432 000 s (5 days, 480 × 900 s intervals) |
| frozen warm point | 24 300 s (6.75 h) |
| prefix reused | **5.62 %** |
| work still simulated after resume | 94.38 % |
| best possible speedup on a cache hit | **1.06×** |

So even in the perfect case — state already cached, zero overhead — the frozen
campaign case can save about one twentieth of a run. The measured v9 result was
worse than that: **cold 91.38 s vs warm 103.15 s, i.e. warm was 12.9 % slower**,
because every identity had to build its own prefix first.

This is not a defect in the code. It is what happens when you snapshot 6.75 hours
into a 120-hour simulation. It does mean the phrase "warm start" is promising
something the current configuration cannot deliver.

## 2. The frozen case is the worst case in the search, and nothing shares a prefix

Running the real closure search (15 schedules from the frozen spec) and asking
`evaluate_warm_eligibility` for each:

| warm point (s) | share of run reused | schedules |
|---|---|---|
| 24 300 / 25 200 / 26 100 | 5.6 – 6.0 % | 3 |
| 110 700 / 111 600 / 112 500 | 25.6 – 26.0 % | 3 |
| 197 100 / 198 000 / 198 900 | 45.6 – 46.0 % | 3 |
| 283 500 / 284 400 / 285 300 | 65.6 – 66.0 % | 3 |
| 369 900 / 370 800 / 371 700 | 85.6 – 86.0 % | 3 |

Two things follow.

**The campaign froze the least favourable schedule.** The whole warming
programme has been validated against a 5.6 % case while 12 of 15 schedules would
reuse 25–86 %. That is defensible as a worst-case test, but it means the
measured "warm is slower" result is not representative of what the search would
see — and nobody has measured the favourable end.

**All 15 warm points are distinct, so no two schedules share a prefix.** With 3
demand variants × 1 repetition × 15 schedules there are **45 identities per
search and 45 separate prefix runs**, because `demand_variant`, `seed` and
`warmup_end_s` are all part of the cache key. Within a single optimization sweep
the cache never hits. Every schedule pays prefix + resume, which is strictly more
work than a cold run.

**The missed opportunity is structural, not accidental.** The prefix is
deliberately *candidate-free* — built from the unfiltered archive route with no
closure additional (`monthly_sumo.py:974`). A state saved at 24 300 s is
therefore a valid starting point for the 110 700 s schedule too: you could load
it once and step forward, instead of re-simulating 0 → 110 700 from scratch. The
design saves one independent state per warm point and never chains them. For a
search that sweeps closure start times across a week, that is the single largest
available win and it is currently left on the table.

## 3. The cache cannot be populated yet, so every run is a miss

States are written as **provisional** and promoted into the cache only after full
canonical equivalence passes:

> `monthly_sumo.py:594` — "States created this run but NOT yet cached. They are
> promoted only after full canonical equivalence passes."
> `run_monthly_warm_state_validation.py:614` — `"cache_material_publishable": passed`

Equivalence has never passed. v9 executed and failed (3 comparisons, 3
mismatches, no cache published); v10, v11 and v12 are frozen but unapproved and
unexecuted. Therefore the warm-state cache is **empty by construction today**,
every lookup misses, and the warm path is guaranteed to be slower than cold until
a paired campaign passes.

That gate is correct — you should not reuse a state that was never shown to
reproduce the cold result. But it means warming currently has a negative speed
effect in every configuration, and will keep having one until the residual
(§ "background" below) is closed.

## 4. The cache key invalidates on almost any change

`WarmStateIdentity` (`warm_state_cache.py:80`) hashes, among other things:

- `git_commit` — populated from `git rev-parse HEAD`
- `source_files` — six interpreting sources via `warm_interpreting_sources()`:
  `monthly_sumo.py`, `monthly_warm_state.py`, `metrics.py`, `envelope.py`,
  `suggest_closure_time.py`, `warm_state_boundary.py`
- `sumo_version`, `python_version`, `platform_id`
- `demand_build_id`, `network_build_id`, `demand_variant`, `seed`,
  `simulation_mode`, `warmup_end_s`

Consequences for day-to-day speed:

- **Every commit throws away every cached state.** Including commits that touch
  documentation or unrelated modules, because `git_commit` is in the key
  independently of the source list.
- **Editing any of the six interpreting sources does the same.** During active
  work on the warming code itself — precisely when you want fast iteration — the
  cache is invalidated by the act of working on it.

Binding the interpreting sources is sound: their bytes change how a state is
*read*. Binding the whole-repository commit hash is much broader than that, and
it is worth asking whether the source digest alone would carry the same safety at
a fraction of the invalidation rate. (I am flagging the trade-off, not asserting
the commit hash is unnecessary.)

## 5. Warming does not touch the interactive close-a-road path at all

`warm_execution=True` is constructed in exactly two places:
`independent_daily_worker.py:75` and `monthly_demand.py` — both inside the
**monthly closure-search backend**.

`run_scenario.py` and `serve.py` — the `/api/close` path behind the 🚧 *Stäng
väg* button, the one that takes ~40 s for a whole-day interactive closure — never
construct a warm runner. They import `warm_state_cache` only for
`save_state_arguments` / `load_state_arguments` helpers.

So: the request "fast road closing simulation" is not addressed by any of this
work. Interactive closures run fully cold today and will continue to, regardless
of what the warming campaigns conclude.

## 6. Per-run overheads that eat into a 6 % budget

These are small individually. They matter only because the budget in § 1 is
itself small.

- **`WARM_POST_FLUSH_S = 3600`** (`monthly_sumo.py:68`). The resumed arm runs to
  `duration_s + 3600`, i.e. one extra simulated hour beyond the cold arm's span.
  Whether the cold arm carries the same margin is worth confirming; if not, the
  warm arm is doing strictly more simulation work than the run it is compared
  against.
- **`WARM_OUTPUT_PRECISION = 16`** (`warm_state_boundary.py:58`) is applied to
  the warm arms (`monthly_sumo.py:1008, 1102`). Sixteen-digit output is larger to
  write and parse than the two-decimal production default. It exists so the
  reconciliation arithmetic is exact, which is the right call — but it is a real
  I/O cost on every warm run.
- **Two SUMO process launches instead of one.** Prefix and resumed are separate
  processes, each paying startup, network load and TraCI connect (the connect
  loop polls up to 40 times at 50 ms). On a 91 s run, launching and loading the
  Gothenburg network twice is not negligible.
- **Per-vehicle TraCI round trips.** `_accumulator_map` calls
  `connection.vehicle.getTimeLoss(v)` once per active vehicle. At the frozen
  boundary that is 44–51 vehicles, which is fine. It scales linearly with
  vehicles in flight, so a busier boundary or an earlier-in-the-peak warm point
  costs proportionally more.

## 7. A correctness note that bears on the speed story

`warm_state_boundary.py:1313` carries this comment in `_restore_phase`:

> "Legacy diagnostic compatibility only. Production v13 passes no saved ledger
> because **TraCI's meso timeLoss is waiting time, not the tripinfo accumulator**
> that must be reconstructed."

If that is right — and it is stated as settled in the code — then the TraCI-based
save/restore ledger measures a *different quantity* from the one the objective is
built on. Production has already moved to reconstructing from the prefix run's
own tripinfo output instead (`reconcile_resumed_tripinfo`). Two observations:

1. The TraCI ledger path is still present, still exercised by tests, and still
   costs per-vehicle round trips wherever it runs. If it is genuinely legacy, it
   is dead weight on the hot path.
2. The comment references "v13" while the current frozen contract is v12. Either
   the comment is ahead of the artifacts or the naming has drifted; either way a
   reader cannot currently tell which mechanism is authoritative from the code
   alone.

I did not attempt to verify the meso `getTimeLoss` semantics myself — that would
need a simulator run, which this review did not do.

---

## Background: why warming is blocked regardless

For context on § 3, the outstanding correctness problem is well characterised and
is *not* vague:

- v9 executed once and failed with residual **−7.73 / −80.62 / −138.97 s** for
  q10/q50/q90 — bit-identical to v2's residual, which predates the
  state-serialization settings v9 applied, refuting that hypothesis.
- The LUNA-WARM-22 forensic diagnostic localized it exactly: **5 of 44, 10 of 50
  and 12 of 51 vehicles in flight across the warm point** carry the entire gap,
  their per-vehicle deltas summing to the residual to the cent. All negative, all
  in the resumed phase, most restored with exactly 0.00 accumulated time loss.
  More than 99.99 % of vehicles are identical between arms.
- **Why those particular vehicles lose their accumulator is still unknown.** v12
  binds a selective, restore-measured correction that repairs only the observed
  deficits, and it is unproven — it needs its own approved paired campaign.

## What I would measure before changing anything

Not recommendations to implement, just the cheapest ways to find out whether the
approach can pay:

1. **Run the favourable end of the search.** Time a cold run against a warm run
   at `warm_point_s = 371 700` (86 % reused). If warm is not decisively faster
   there, the approach cannot pay anywhere and the remaining questions are moot.
2. **Test whether one prefix can serve many schedules.** Load the 24 300 s state,
   step to 110 700 s, and compare against a cold run to 110 700 s. If that
   reproduces, prefix chaining collapses 15 prefix runs into 1 and is worth far
   more than any micro-optimization in this document.
3. **Measure the fixed overhead alone.** Time a warm run whose warm point is ~0.
   Whatever that costs is the floor every warm run pays, and it tells you the
   minimum reuse fraction at which warming can break even.
4. **Decide what the target actually is.** If the goal is a faster *interactive*
   closure (§ 5), none of this machinery applies and the work would start
   somewhere else entirely.

## Summary table

| # | Finding | Speed impact | Kind |
|---|---|---|---|
| 1 | Prefix reuses 5.6 % of the frozen case; max 1.06× | Caps the upside | Design |
| 2 | 15 distinct warm points, no prefix sharing, 45 prefix runs/search | Makes optimization slower than cold | Design |
| 3 | Cache only populated after equivalence passes; never has | Guarantees 100 % miss rate today | Gate, correct but blocking |
| 4 | `git_commit` + 6 sources in the cache key | Invalidates on any commit | Trade-off |
| 5 | Interactive close-a-road path never warms | Requested use case unaddressed | Scope |
| 6 | Extra flush hour, 16-digit output, 2 launches, per-vehicle TraCI | Erodes a small budget | Overhead |
| 7 | TraCI ledger may measure the wrong quantity; "v13" vs v12 naming | Dead weight, unclear authority | Clarity |

**Bottom line:** the warming code is careful, well-tested and honest about what
it has not proven. What it cannot currently do is make anything faster — the
frozen configuration caps the gain at ~6 %, the search shape prevents cache
reuse, the promotion gate keeps the cache empty, and the interactive path is not
wired to it at all. Finding 2 is the one I would look at first: the prefix is
already candidate-free, so chaining prefixes across schedules is the one change
that could turn a 1.06× ceiling into something worth having.
