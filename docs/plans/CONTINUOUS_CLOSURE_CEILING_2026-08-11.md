# The 21-workday continuous ceiling: what changing it would cost

Written 2026-08-11 as a separate document on purpose. Stage 4 of the
closure-search scaling plan measured the independent-vs-continuous question and
found 24 of its 84 pre-registered cases unmeasurable because
`ClosureSearchSpec` refuses a continuous closure longer than 21 workdays. The
temptation is to treat that as a benchmark obstacle and raise the constant. It
is not an obstacle; it is a product decision with consequences, and it must not
be made as a side effect of wanting a comparison to run.

Nothing in this document has been implemented. It exists so that a future
decision to lift the ceiling is taken deliberately and with its price visible.

## Where the ceiling lives

`traffic_sim/core/contracts.py`:

```python
_CONTINUOUS_MAX_WORKDAYS = 21
_INDEPENDENT_MAX_WORKDAYS = 90
_DEMAND_PURPOSE_MAX_DAYS = {"standard": 7, "closure_envelope": 24}
```

`ClosureSearchSpec.__post_init__` refuses `max_consecutive_start_days > 21`
unless `interday_policy == "independent_daily_reset_v1"`, and additionally
requires `exact_equal_daily_v1` above that threshold. So the two policies are
not two ways of doing the same thing at any length: above 21 workdays there is
only one of them.

## Why 21, concretely

A continuous closure is simulated as ONE envelope: warm-up, the whole closure,
and the recovery tail, in a single SUMO run against a single calibrated demand
archive spanning every day of it. That is why the demand contract allows
`closure_envelope` builds of at most 24 days — 21 closure days plus warm-up
and recovery headroom.

The independent policy has no such envelope. It decomposes the parent into
daily units, each an isolated single-day archive, and reuses units across
parents. Its 90-day ceiling costs nothing extra per day beyond the days
themselves.

## What lifting it to 90 would actually require

1. **Demand.** `_DEMAND_PURPOSE_MAX_DAYS["closure_envelope"]` would have to
   rise from 24 to at least 93. Every continuous candidate then needs a
   93-day calibrated q10/q50/q90 archive. These archives are not decomposable
   and not shared between candidates the way daily units are, so the demand
   build cost scales with the number of distinct envelope windows, not with
   the number of distinct days.
2. **Simulation.** One SUMO process must hold a 93-day mesoscopic run. The
   longest continuity evidence this project has is the frozen 7-day golden
   release, plus a 9-day resource proof (`tools/benchmark_nine_day_envelope.py`).
   A 93-day envelope is an order of magnitude beyond anything measured; its
   memory and wall-time behaviour is unknown, not merely large.
3. **Validation.** The 7-day golden continuity freeze would no longer cover the
   range being run. Either a new long-envelope continuity freeze is produced,
   or every result above 7 days is labelled as running beyond its validation —
   which the search already does, but at 93 days that label carries far more
   weight than at 14.
4. **Recovery semantics.** `evaluate_recovery` asks whether the network
   returns to baseline within the recovery cap after closure end. Over a
   3-month closure the "baseline" is a seasonal moving target the current
   contract does not model, so the recovery verdict would be comparing against
   a reference that drifted underneath it.

## What it would NOT fix

Stage 4 measured a second divergence that has nothing to do with the ceiling.
Even below 21 workdays the two policies enumerate different candidate spaces,
in both directions:

* `equal_daily_rounded_v1` rounds each daily shift UP to the 15-minute
  resolution, so the continuous arm can serve the same work requirement in
  FEWER days. The 21-workday midday case enumerates 17-, 18-, 19-, 20- and
  21-day schedules — 470 candidates against the independent arm's 150 — and
  the short ones schedule up to 5130 minutes for a 5040-minute requirement.
  `exact_equal_daily_v1` cannot express any of them, because 5040/17 is not a
  multiple of the resolution.
* The independent policy walks consecutive ELIGIBLE dates, so with weekends
  excluded it can straddle a weekend where calendar-consecutive continuous
  cannot: 8 candidates against 6 on the 3-workday weekdays-only cases.

Raising `_CONTINUOUS_MAX_WORKDAYS` would extend the comparison's range without
making the two arms search the same space. A comparison that is honest about
this is worth more than a wider one that is not, which is why
`tools/measure_independent_vs_continuous.py` records
`candidate_space_identical` per case and refuses to call any case low-risk
when it is false.

## The recommendation, if it is ever asked for

Do not raise the ceiling for a benchmark. Raise it only if a real user needs a
continuous closure longer than three weeks — and if that happens, treat items
1-4 above as the work, with the 93-day envelope's resource behaviour measured
before anything is promised. Until then, 22-90 workdays remains an
independent-daily-only range, and no 1-21 day result may be extrapolated into
it. That extrapolation rule is frozen in
`validation/independent_vs_continuous_preregistration_v1.json` and carried
unchanged into
`validation/independent_vs_continuous_outcome_v1.json`.
