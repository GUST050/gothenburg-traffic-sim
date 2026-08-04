# Warming code — second review: correctness bugs

**Date:** 2026-08-04 · **Reviewer:** Luna High (Claude) · **Status:** findings
only. Nothing fixed, no production file changed.

This is a second pass over the same code as
`WARMING_SPEED_REVIEW_2026-08-03.md`, looking for *defects* rather than design
and speed limits. None of the findings below overlap with that document.

Everything here was probed against the real functions with synthetic fixtures —
no simulator was run. Where a finding depends on something I could not verify
without SUMO, I say so explicitly rather than asserting it.

> **Verified disposition added 2026-08-04.** Findings 1–2 are not SUMO bugs:
> official SUMO 1.27 source stores mesoscopic tripinfo `timeLoss` as integer
> `SUMOTime` and formats it through `time2string`, whose integer-millisecond
> conversion is half-up. That matches `normalize_time_loss`; the review's
> `%f`/binary-double premise does not describe this code path. Finding 4 is
> fixed: resumed commands explicitly require
> `--tripinfo-output.write-unfinished true`, and the validator rejects false.
> Findings 5–6 are clarified at the producer and consumer: the resumed-only and
> whole-run continued totals have named local roles, production cross-checks the
> resumed total against post metrics, and `corrected_count` is documented as a
> population size rather than independent evidence. Finding 7's stale version
> reference is gone; production passes no saved TraCI ledger and therefore does
> no per-vehicle time-loss round trips. The legacy ledger remains diagnostic-
> only. Sources: [MSDevice_Tripinfo.cpp](https://github.com/eclipse-sumo/sumo/blob/v1_27_0/src/microsim/devices/MSDevice_Tripinfo.cpp),
> [SUMOTime.cpp](https://github.com/eclipse-sumo/sumo/blob/v1_27_0/src/utils/common/SUMOTime.cpp),
> and [TripInfo output](https://sumo.dlr.de/docs/Simulation/Output/TripInfo.html).

---

## Finding 1 — [HIGH] The warm arm rounds differently from the cold arm

`normalize_time_loss` (`warm_state_boundary.py:88`) rounds with
`Decimal(str(x)).quantize(..., ROUND_HALF_UP)`. SUMO writes tripinfo with C
`%.*f`, which rounds half-to-even on the binary double. **These are different
functions and they disagree.**

Measured, 240 000 samples:

| rounding path | vs SUMO `%.2f` |
|---|---|
| Python `round(x, 2)` — random continuous values | 0 disagreements / 200 000 |
| Python `round(x, 2)` — dyadic values `k/8` | 0 disagreements / 40 000 |
| `normalize_time_loss(x, 2)` — random continuous | 0 disagreements / 200 000 |
| `normalize_time_loss(x, 2)` — dyadic values `k/8` | **10 000 disagreements / 40 000 (25 %)** |

Concrete cases:

| exact value | SUMO `%.2f` | `normalize_time_loss` |
|---|---|---|
| 0.125 | 0.12 | **0.13** |
| 0.625 | 0.62 | **0.63** |
| 8.125 | 8.12 | **8.13** |
| 3.045 | 3.04 | **3.05** |
| 12.665 | 12.66 | **12.67** |

Why this matters more than it looks:

- **Every** warm tripinfo value passes through `normalize_time_loss`
  (`reconcile_resumed_tripinfo`, line 462), including the ~99.99 % of vehicles
  that are *not* boundary-active and receive `+ 0.0` — they are rounded by
  Python from the 16-digit warm output. The cold arm's values are rounded by
  SUMO. So the two arms use different rounding for essentially every vehicle.
- The comparison policy demands **exact** semantic equality. A single vehicle
  landing on a representable midpoint shifts the total by 0.01 s and fails the
  campaign.
- `_read_warm_edgedata_time_loss` (`monthly_sumo.py:182`) uses plain
  `round(value, TRIPINFO_PRECISION)`, which **does** match SUMO. So the codebase
  already contains the rounding that agrees — just not on the tripinfo path.

**What I could not verify:** whether SUMO's mesoscopic `timeLoss` actually
produces exactly-representable midpoints (`x.125`, `x.625`, …). With
`--step-length 1.0` and queue-based meso delays it is plausible but unproven,
and it would need a real run to settle. If meso timeLoss is always continuous
noise, the divergence never fires; if it is quantised to eighths or quarters,
roughly a quarter of midpoint values are wrong. **This is the first thing I
would check against the existing residual-v2 per-vehicle data**, which is
already on disk and needs no new campaign.

## Finding 2 — [MEDIUM] Two rounding helpers inside the warm path, one silently unlike the other

Directly following from Finding 1: the warm path now contains two different
"round to production precision" operations.

- `_read_warm_edgedata_time_loss` → `round()` → matches SUMO. Its docstring says
  *"Repeat exactly that operation here"*, and it does.
- `reconcile_resumed_tripinfo` / `build_prefix_accumulator` →
  `normalize_time_loss` → does not.

Nothing in the code or tests asserts that these two agree, and nothing explains
why one path deliberately uses half-up. Given how carefully the rest of this
module documents its choices, my reading is that `ROUND_HALF_UP` was chosen for
being "obviously correct" arithmetic rather than for matching SUMO — which is
the wrong target when the whole point is reproducing a cold run byte-for-byte.

## Finding 3 — [NEGATIVE RESULT] The join arithmetic itself is exact

Worth recording because it narrows the search: I ran 200 randomised split-and-
rejoin trials — a synthetic cold population partitioned into completed-prefix /
boundary-active / post-boundary, reassembled through
`reconcile_resumed_tripinfo` — and compared against the uninterrupted cold total.

```
trials: 200 | totals differ in 0 | worst |delta| = 0.000000 s
```

**The prefix + resumed reconstruction reproduces the cold total bit-exactly when
the inputs are exact.** The v9 residual is therefore not coming from the join
arithmetic, the summation order, or the `start=` accumulator trick. That is a
real point in the design's favour and it rules out a whole class of suspicion.

## Finding 4 — [MEDIUM] An implicit dependency on `write-unfinished` that nothing pins

`reconcile_resumed_tripinfo` treats a boundary-active vehicle missing from the
resumed tripinfo as fatal:

```
resumed tripinfo is missing boundary vehicles: ['act1']
```

(verified by probe). That is correct behaviour — the deficit could not be
applied — but it means the warm arm depends entirely on the resumed run being
invoked with `tripinfo_write_unfinished=True` so that vehicles still in flight
at the end are written out.

Today that holds by *default* (`run_scenario.py:1461`), and the resumed call
site simply does not pass the flag. The prefix deliberately passes `False`. So
the correctness of the warm path rests on a default that the adjacent code
overrides for a different arm — and no test asserts the resumed arm must have it
`True`. A future caller that standardises the flag across both arms would break
every warm run that has any vehicle in flight at the end of the horizon, and the
failure would present as an unexplained cold fallback.

## Finding 5 — [LOW] Two near-identical total fields, one of which double-counts if picked wrongly

`reconcile_resumed_tripinfo` returns both:

- `corrected_total_time_loss_s` — resumed vehicles only
- `combined_total_time_loss_s` — resumed **plus** `completed_prefix_total`

Production correctly uses the first (`monthly_sumo.py:1280`), because
`reconstruct_metrics` adds the prefix aggregate separately. Using the second
there would double-count the entire prefix. The names differ by one word, both
are exported in the same dict, and nothing marks which one is the objective.
This is a latent foot-gun rather than a present bug.

## Finding 6 — [LOW] `corrected_count` cannot ever differ from `active_count`

```python
"corrected_count": sum(identifier in active for identifier in records)
```

The `missing` check immediately above guarantees `active ⊆ records`, so this
always equals `len(active)` — which is already reported as `active_count`. It
reads like a cross-check but cannot fail, so it provides no evidence.

## Finding 7 — [LOW] Stale forward reference and a legacy path still on the hot line

`warm_state_boundary.py:1313`:

> "Legacy diagnostic compatibility only. **Production v13** passes no saved
> ledger because TraCI's meso timeLoss is waiting time, not the tripinfo
> accumulator that must be reconstructed."

Two problems. The current frozen contract is **v12**, so a reader cannot tell
whether this comment is aspirational, from an abandoned branch, or authoritative.
And the legacy TraCI save/restore ledger it refers to
(`capture_save_ledger` / `capture_restore_ledger` / `build_restore_audit`) is
still present, still wired into `run_prefix`, and still costs one TraCI round
trip per active vehicle on every warm run — for a measurement the comment says
is of the wrong quantity.

If that comment is right, the ledger is dead weight on the path that is supposed
to be fast. If it is wrong, the production reconstruction is missing a
correction it should be applying. Either way the ambiguity is worth resolving,
and it cannot be resolved from the code alone.

---

## Summary

| # | Severity | Finding | Verified how |
|---|---|---|---|
| 1 | **High** | Warm arm rounds `ROUND_HALF_UP`; cold arm (SUMO) rounds half-even. 25 % divergence on dyadic values | 240 000-sample probe |
| 2 | Medium | Warm edgeData uses matching `round()`; warm tripinfo does not. No test asserts agreement | source read |
| 3 | — | *Negative result:* join arithmetic reproduces cold totals bit-exactly | 200 randomised trials |
| 4 | Medium | Warm arm silently depends on resumed `write-unfinished=True`; unpinned | probe + source |
| 5 | Low | `corrected_` vs `combined_` totals; wrong pick double-counts the prefix | source read |
| 6 | Low | `corrected_count` is a check that cannot fail | source read |
| 7 | Low | Comment cites "v13" (current is v12); legacy TraCI ledger still on the hot path | source read |

**Where I would look first.** Finding 1 is the only one that could plausibly
explain a persistent, small, systematically-signed residual — which is exactly
the symptom v9 produced. It is also cheap to test: the residual-v2 outcome
already holds per-vehicle `timeLoss` values for ~260 000 vehicle-records across
three identities. Checking how many of them are exactly representable midpoints
would confirm or eliminate it in minutes, without a campaign.

I want to be careful not to overstate that: the LUNA-WARM-22 localisation showed
the residual concentrated in 5/10/12 *boundary-active* vehicles with large
individual deltas (up to 55 s), which a 0.01 s rounding difference cannot
produce. So Finding 1 is **not** an explanation of the known residual. It is a
separate latent defect that would break exact equality independently, and would
do so even after the boundary-accumulator problem is fixed.
