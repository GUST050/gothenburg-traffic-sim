# What still needs doing before starting the annual warming

**Date:** 2026-08-04 · **Reviewer:** Luna High (Claude) · **Status:** reviewed,
fixes implemented; final population blocked on disk preflight.

Scope: the working-tree diff and the annual-warming subsystem as it stands
today, read specifically as "is this safe and worth launching now?". The thing
about to be started is:

```
python3 tools/populate_annual_warming.py --execute --plan-key <key> --state-workers 3
```

against the then-current plan `0ce86bca…98d10` — **104,685 work units, 367 three-day demand
builds, 363 eligible dates**. That is a multi-day, largely unattended compute
job, so the bar for "is it ready" is higher than for a normal change.

## Disk blocker resolved — 2026-08-04 (later same day)

**The run is unblocked.** Preflight passes: 55.8 GiB required against 168.3 GiB
free, 112.5 GiB headroom. Root `8f05020248f4…c467d` is initialized with 104,685
pending and zero attempts.

Root cause of the 192 GiB figure: it was dominated by *retaining all 367
three-day demand archives at once* (367 × 326 MiB ≈ 117 GiB), not by the
artifacts (≈42 GiB). The archives are redundant — `pack_artifact` already binds
the route, demand meta, build spec and manifest as content-addressed blobs, and
the group loop only resolves an archive when the build still has selectable
units.

Three changes:

1. **Prune each demand archive once its build's units are all durably
   succeeded** (`_prune_demand_archive`). Refuses if any unit for that build is
   still selectable, if the path is outside `runs/`, if it is not named
   `demand-*`, or if it is a symlink. `--keep-demand-archives` opts out.
   117 GiB → 0.6 GiB peak.
2. **Proportional disk gate** (`required_free_bytes`). The flat whole-year
   constant refused bounded pilots for archives they would never build, so
   `--max-units` / `--demand-build-key` / `--variant` were unusable on any
   realistic disk. The requirement is now derived from selectable units at a
   measured per-unit rate.
3. **LZMA as a third store encoding**, chosen per member from measured output
   size. Route `6,371,443 → 950,432` B (14.9%), prefix evidence
   `181,037 → 110,204` B (60.9%). SUMO's already-gzipped state correctly stays
   `identity` — a ratio guard skips the LZMA attempt so no chain pays CPU for
   zero bytes saved.

| | before | after |
|---|---|---|
| demand archives (peak) | 116.8 GiB | **0.6 GiB** |
| artifact store (full year) | 41.4 GiB | **29.4 GiB** |
| **projected peak** | **166.3 GiB** | **42.0 GiB** |
| preflight demands | 192 GiB flat | **55.8 GiB** (conservative: charges the unsealed rate) |

Checks: annual + warm suites **343 passed**; plan and preflight re-frozen and
verifying; `git diff --check` clean. New coverage: 13 tests for the gate and the
prune guard, plus two end-to-end tests proving `execute_population` actually
releases a completed build's archive and that `--keep-demand-archives` retains it.

**Measured but not implemented — a further 3.2×.** `prefix_evidence.json` stores
a *cumulative* record at every link: 97.8 MB of JSON per chain carrying 665 KB
of new information (0.9%). Compressing a whole chain as one stream instead of
per member collapses it — evidence 16.26 MB → 0.23 MB, states 17.77 MB →
2.79 MB — taking the store from 29.4 GiB to ~4.1 GiB and the peak to ~13 GiB.
Two caveats found while measuring: containers must group like members together
(interleaving states and evidence gives 16.6 MB instead of 3.0 MB), and the
state saving only materialises if states are stored *expanded* — which means
asking SUMO for uncompressed state XML and therefore **re-running the 96-link
chain audit**. That is why it is recorded here rather than done: it would
invalidate a passing audit immediately before the run.

## Implementation disposition — 2026-08-04

**Current decision: do not start the annual population yet.** The code and the
96-link correctness audit now pass, but the review's disk projection was too
optimistic. A measured canonical three-day archive occupies 326 MiB and a
single q10 96-link artifact store occupies 40 MiB. The old 160-GiB gate left
effectively no margin once 367 archives, q10/q50/q90 states, staging and the
8-GiB runtime reserve are included. The initial gate is now 192 GiB.

The final source-bound plan is `9cc823d3…45283b`. Its preflight correctly fails:
206,158,430,208 bytes are required and 180,475,920,384 bytes are currently
free. At least 25,682,509,824 bytes (23.92 GiB) must be freed before a production
root can be initialized. No unit under this final plan has run.

| Review item | Disposition and evidence |
|---|---|
| §1, 96-link chaining | Fixed. Extensions use exact departure-window route shards rather than reparsing the full three-day route, preventing route definitions from accumulating in saved state. Private mesoscopic time loss is carried on SUMO's native millisecond grid, with a 96-handoff regression. `annual_warm_chain_pilot_v4.json` passes all 96 restores and independent cold checks at links 2/48/96. Every outcome-relevant section is exact; expanded states stay 1.24–1.59 MiB and selected chained/cold size ratios are 0.998/0.954/0.988. |
| §2, repeated compression | Fixed. The content-addressed store reuses and verifies an existing gzip or identity blob before compression; unchanged blobs are decompressed at most once per process. |
| §3, timing and one-process batching | Measured with a real 96-link chain. One-process multi-snapshot batching is not adopted: unfinished tripinfo is finalized when SUMO exits, while SUMO save-state omits the private mesoscopic tripinfo accumulator. Taking 96 states in one live process would therefore lose the exact per-checkpoint evidence the current contract requires. Route sharding removes the route-parse/state-growth defect without weakening that contract. |
| §4, disk | Original estimate superseded. The gate is raised from 160 to 192 GiB and now fails safely on this machine. This is the only operational blocker before population. |
| §5, held-out gate | Synthetic mechanism tests remain non-vacuous and pass (`176 passed`). Frozen v4/v5/v6 evidence remains intentionally source-stale and fail-closed; it was not rewritten to manufacture current release authority. This is orthogonal to candidate-free population and still blocks unsupported global-best claims. |
| §6, stale demand archives | Fixed as a class. Archive validation now binds the complete current demand-source inventory, Python/SUMO runtime, all ten output hashes, candidate provenance and exact 7,125-edge support. A current three-day archive was rebuilt and validated; stale July archives are not selectable. A live source seal is checked before every demand-build group. |
| §7, stale readiness command | Retired. `annual_warm_readiness_v1.json` is historical evidence and must not be used. The only eligible future command uses plan `9cc823d3…45283b`, but it remains blocked until the current preflight passes and the matching zero-attempt root is initialized. |

One byte-level difference is deliberately visible in the final chain audit:
the cold full-route loader had parsed 4/55/14 future vehicle definitions at the
three checkpoints, while the exact route-window arm had not. Inserted and
teleport counters, every vehicle identity/value, time-loss total, queue,
recovery bucket, snapshot fact and order are exact. The audit permits only this
bounded `loaded` lookahead difference and has regression tests proving that no
other counter or evidence mismatch is accepted.

Research basis: SUMO documents that saved states do not contain future route
departures and therefore still require route input on load
([SaveAndLoad](https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html)); unfinished
tripinfo is written when the simulation ends
([TripInfo output](https://sumo.dlr.de/docs/Simulation/Output/TripInfo.html)).
The SUMO mesoscopic tripinfo source stores `myMesoTimeLoss` as integer
`SUMOTime` and advances it through `TIME2STEPS`; it is not serialized in the
device's save-state payload. Those facts are why route windows and millisecond
handoffs are safe, while the proposed one-process evidence batching is not.

Checks after the fixes: focused annual/store/progress/population/boundary/route
suite `164 passed`; focused boundary/route/audit suite `125 passed`; held-out
gate suite `176 passed`; `git diff --check` passes. A repository-wide run also
exposes intentionally stale historical freeze/source-binding tests, so it is
not represented as a blanket all-tests-green claim.

This supersedes nothing in `WARMING_SPEED_REVIEW_2026-08-03.md` or
`WARMING_BUG_REVIEW_2026-08-04.md`; those findings were about the monthly warm
path. One finding in `DEMAND_PIPELINE_REVIEW_2026-08-04.md` is now wrong and is
corrected in §0.

---

## 0. Correction to my own demand review

**Finding S1 of `DEMAND_PIPELINE_REVIEW_2026-08-04.md` is already implemented.**
I reported the per-quarter `touch` map rebuild as an unrealized ~54 % saving. It
is in the working tree:

- `traffic_sim/demand/pfe.py:147` — `build_touch_index(cands)`
- `demand/calibration.py:274` — `_PFE_PAR_TOUCH_INDEX = pfe.build_touch_index(shapes)`,
  set **before the pool forks**, exactly the shape the finding called for
- threaded through `solve_interval_entropy` / `_with_relaxation` /
  `_with_structure_guard` as an optional `touch_index`, with the old
  reconstruction kept as the `None` fallback

Measured effect, from `demand_meta.json` timings on today's 1-day builds:

| | before | now |
|---|---|---|
| `pfe_variants_and_rounding` | 173.1 s | **57.5 – 59.6 s** |

`tests/test_pfe.py::test_precomputed_touch_index_is_bitwise_equivalent` pins the
exactness. Findings S2 (non-binding ceilings) and S3 (no kernel early exit,
`pfe_kernel.py:68` is still a bare `for it in range(max_iterations)`) remain
open.

---

## 1. The chain is 96 links deep and has only ever been tested one link deep

This is the finding I would act on before anything else.

The plan chains checkpoints within each (demand build, seed, variant):

```
demand builds with checkpoints : 367
chain depth (checkpoints/build): min 5, median 96, max 96
checkpoint spacing             : 900 s, from 3,600 s to 171,000 s
```

Every chaining diagnostic on disk starts from `from_s = 24300` — a
**cold-produced** cache entry — and takes exactly **one** extension:

| diagnostic | from → to | segment | chained | cold | chained/cold |
|---|---|---|---|---|---|
| `..._900s_diagnostic_v1` | 24,300 → 25,200 | 900 s | 1.663 s | 1.841 s | **1.11×** |
| `..._900s_final_audit_v1` | 24,300 → 25,200 | 900 s | 1.686 s | 1.821 s | **1.08×** |
| `..._chaining_diagnostic_v1` (q10) | 24,300 → 110,700 | 86,400 s | 5.290 s | 4.762 s | 0.90× |
| `..._q50_diagnostic_v1` | 24,300 → 110,700 | 86,400 s | 6.181 s | 5.221 s | 0.85× |
| `..._q90_diagnostic_v1` | 24,300 → 110,700 | 86,400 s | 6.396 s | 5.348 s | 0.84× |
| `..._favourable_diagnostic_v1` | 24,300 → 371,700 | 347,400 s | 16.606 s | 14.582 s | 0.88× |

Two things to take from this table.

**No link-2 evidence exists.** Every row's predecessor was produced cold. The
production run will build link 2 from link 1's *chained* output, link 3 from
link 2's, up to link 96. The property being relied on — that extending a
chained state is as exact as extending a cold state — is exactly the property
never tested. The exactness results are genuinely strong (all ten
`prefix_evidence` sections byte-equal, active accumulator equal to 0.00 s across
45–63 vehicles), but they are strong *at depth 1*.

**The chained state is materially bigger than the cold state at the same
instant**, and nobody has checked whether that compounds:

| checkpoint | cold expanded | chained expanded | ratio |
|---|---|---|---|
| 25,200 s | 560,162 B | 1,863,048 B | **3.33×** |
| 110,700 s | 758,518 B | 2,061,404 B | **2.72×** |
| 371,700 s | 831,961 B | 2,134,847 B | **2.57×** |

`expanded_state_equal` is `false` in all of them, with
`expanded_state_bytes_are_release_gate = false` and the semantic sections equal
— so the artifacts are internally consistent and honest about this. But an
inflation factor that is already ~3× after **one** link, on a chain that will be
**96** links long, is an unmeasured risk to both storage and per-link
save/load time. If the factor is a one-off (chained states carry something cold
states omit, then stop growing) it is harmless. If it compounds even slightly
per link, the tail of every chain degrades.

**What I would do:** run one complete day-chain — a single (demand build, seed,
variant) walked through all 96 checkpoints — and check (a) exactness at links 2,
48 and 96 against cold states at the same instants, and (b) whether
`expanded_bytes` grows with link index. That is ~96 units out of 104,685
(0.09 % of the run) and it converts the single largest unknown into evidence.
It also produces the timing number §3 is missing.

## 2. The store recompresses every member before checking whether it already exists

`AnnualWarmArtifactStore._store_member` (`annual_warm_store.py:127`) does, in
order:

1. `_sha256_file(source)` — full read of the member
2. **unconditionally** gzip the entire file at level 6 into staging, `fsync` it
3. `_sha256_file(gzip_path)` — hash the compressed output
4. *then* `_publish_blob`, which finds `target.exists()`, hashes the existing
   blob a third time, and discards the temp file it just built

The member that matters is `route.rou.xml`. Every one of a build's ~96
checkpoints × 3 variants stores the *same* route file, and it is content-
addressed — so from the second checkpoint onward, the compression is pure waste.

Measured on the real `calibrated_v2.rou.xml` (27.0 MB, 1-day build):

```
sha256 source        0.012 s
gzip level 6         0.250 s   -> 2.39 MB
sha256 of gz         0.001 s
existing-blob rehash 0.001 s
-------------------------------
per unit             0.263 s
```

| | serial | at 3 workers |
|---|---|---|
| 104,685 units, 1-day-sized routes (27 MB) | **7.7 h** | 2.6 h |
| 104,685 units, 3-day-sized routes (~78 MB) | **~23 h** | ~7.7 h |

The plan is 357 three-day builds, so the second row is the applicable one. The
fix is a single `exists()` check on both candidate blob paths before compressing;
the encoding choice can be read off whichever blob is already present. Nothing
about the integrity model changes — the blob is still verified by digest on
publish and on restore.

## 3. There is no end-to-end timing evidence for even one full day

Both pilots are 3 units:

- `annual_warm_storage_pilot_v1` — 3 artifacts, `sumo_executed: false`,
  compression/restore only
- `annual_warm_population_pilot_v1` — 3 artifacts, all at the same checkpoint
  (24,300 s), one per variant — i.e. **three link-1 bootstraps, no chain at all**

Neither ledger records a duration. So the only basis for "how long will this
take" is extrapolation. Mine, stated with its assumptions:

| stage | basis | estimate |
|---|---|---|
| 367 three-day demand builds | 1-day measured: PFE 57.5 s, candidates 21.2 s, assignment priors 55.4 s (cold cache) | **25 – 41 h** |
| 104,685 state units at 3 workers | 1.7 s measured per 900 s chained link on 1-day inputs, scaled for 3-day routes + store + validate | **~58 h** |
| **total** | | **≈ 80 – 100 h (3.5 – 4 days)** |

I want to be clear that the second row is the weak one: it scales a
single measured 1.686 s link — taken at 24,300 s on a 1-day route file with 61
vehicles in flight — to units with ~3× the route bytes at busier times of day.
It could be off by a factor of two either way. The one-day pilot in §1 fixes
this for free.

**The structural point behind that row:** the design spends **one SUMO process
per unit** — each link loads its predecessor with `--load-state` and re-parses
the full network (16 MB) and route file (~78 MB for a 3-day build) to advance
900 s. That is 104,685 launches and 104,685 route parses to produce 34,895
distinct checkpoints × 3. `run_prefix` already calls `simulation.saveState`
through a live TraCI connection, so one process per *(build, seed, variant)*
could walk the day once and snapshot all 96 checkpoints — **1,101 launches
instead of 104,685**, a ~95× reduction in launch and parse overhead, without
changing what gets stored. The per-unit ledger semantics can be preserved by
publishing each checkpoint as it is taken. This is the largest architectural
saving available and it is worth measuring before committing 4 days of compute
to the current shape.

## 4. Disk is feasible, but nothing prunes and the margin is thinner than it looks

`MINIMUM_FREE_BYTES = 160 GiB` (`populate_annual_warming.py:71`) is a hard
constant, and `RUNTIME_FREE_RESERVE_BYTES = 8 GiB` aborts the run before
exhaustion. My independent projection of what the run will actually write:

| item | basis | projected |
|---|---|---|
| 367 three-day demand archives | measured 95 MB for a 1-day archive | **~103 GB** |
| state + prefix evidence, 104,685 units | measured 75.8 KB + 44.7 KB per artifact | ~12.6 GB |
| route blobs (content-shared per build×variant) | ~7.2 MB × 367 × 3 | ~7.9 GB |
| **total new** | | **~124 GB** |

Against 181.3 GB free now, minus the 8.6 GB abort floor, that leaves roughly
**49 GB of margin** — workable, and the 160 GiB gate was clearly sized for this
plan rather than the old one. Two caveats:

- **Nothing prunes demand archives.** `_resolve_demand_archive` finds-or-builds
  and they accumulate in `runs/`, which is already 30 GB across 126 archives.
  The 103 GB is retained for the whole run and afterwards.
- The 3-day archive size is extrapolated ×3 from a measured 1-day archive. If
  three-day route files are worse than linear, the margin shrinks fast, and the
  failure mode is an abort at ~96 % completion.

Storage per *checkpoint* is genuinely cheap — the route blob is shared across a
build's whole chain, so the marginal cost of an extra checkpoint is ~121 KB.
That part of the design is working exactly as intended.

## 5. The held-out gate is currently disabled, and by more than the audit says

`WARMING_FINAL_AUDIT_2026-08-03.md` records "Six additional held-out-gate tests
fail closed because their frozen campaign fingerprints predate the source
changes". Current count is **11**, and they are not all fingerprint bookkeeping:

```
FAILED tests/test_heldout_gate.py::TestHappyPath::test_both_valid_artifacts_adopt
FAILED tests/test_heldout_gate.py::TestLiveSourceFingerprintEnforcement::test_current_v6_fingerprints_do_match_the_live_tree
FAILED tests/test_heldout_gate.py::TestProductionRecordCompatibility::test_production_shaped_record_adopts
FAILED tests/test_heldout_gate.py::TestProductionRecordCompatibility::test_nullable_diagnostic_metric_is_allowed
FAILED tests/test_heldout_gate.py::TestChecksAreRecomputedNotTrusted::test_vacuous_failure_recall_is_allowed_when_nothing_was_disqualified
FAILED tests/test_heldout_gate.py::TestRealEvaluatorToLoader::test_real_producer_record_is_adopted_by_the_loader
FAILED tests/test_heldout_v4_freeze.py::test_v4_recorded_enforcement_hashes_remain_frozen
FAILED tests/test_heldout_v5_freeze.py::TestSourceFingerprints::test_the_spent_campaign_no_longer_matches_the_live_tree
FAILED tests/test_heldout_v5_freeze.py::TestSourceFingerprints::test_the_drift_is_specific_not_a_blanket_mismatch
FAILED tests/test_heldout_v6_freeze.py::TestSourceFingerprintsAndReproduction::test_every_fingerprint_matches_the_live_file
FAILED tests/test_heldout_v6_freeze.py::TestSourceFingerprintsAndReproduction::test_freeze_reproduces_byte_for_byte_without_writing
```

Single root cause, verified: `heldout_gate._source_fingerprints_match` requires
*every* fingerprint the frozen manifest recorded to still match the live file.
The tree has drifted from the v6 manifest, so `load_adopted_gate` returns `None`
for **any** input — including the synthetic valid pair the happy-path test
constructs (`assert record is not None` → `None`).

This is fail-closed and safe: the product falls back to bounded-exhaustive
screening and forbids global-best claims. But the practical consequence is that
**monthly global-best claims are off right now**, and the audit's "six …
historical certificate drift" understates both the count and the kind. It does
not block the population run — it is orthogonal — but it should not be
discovered later and mistaken for damage the run caused.

## 6. Demand archives are not bound to the code that produced them — and one stale archive will be reused

This is the most concrete correctness problem I found, and it is verifiable in
one command.

`DemandBuildSpec.build_key` (`contracts.py:212`) hashes **only** calendar
identity:

```python
payload = {"start_date", "source", "days", "begin", "end",
           "structural_reference_date"}   # + purpose if non-standard
```

No source fingerprint. `validate_demand_archive` (`monthly_demand.py:158`)
then checks manifest status, spec equality, build-key consistency,
epoch/duration, variant presence, and each output's sha256 **against its own
manifest** — i.e. internal integrity, not provenance. `find_demand_archives`
returns every passing archive newest-first, and `_resolve_demand_archive`
takes the newest rather than building.

So an archive built by *any* version of `build_candidates.py` / `pfe.py`
satisfies a plan build as long as the dates match. Checking the live tree
against the plan's 367 build keys:

```
plan demand build_keys                        : 367
existing runs/ archives matching a plan build : 3   (all build_key 96269d0654122378,
                                                     2027-07-15, 3-day, forecast)
  runs/demand-20260719-204504-6da5202f-2e8e   mtime 2026-07-19 23:00
  runs/demand-20260721-223623-d353b830-48b8   mtime 2026-07-22 00:37
  runs/demand-20260722-140116-62833a49-1ce1   mtime 2026-07-22 16:02
```

All three predate today's every-edge candidate rebuild, and their
`demand_meta.json` proves it — they lack both blocks a current build writes:

| key | 2026-07-22 archive | today's build |
|---|---|---|
| `candidate_provenance` | **absent** | present (16,542 candidate records, 65,563 vehicles) |
| `edge_support_augmentation` | **absent** | present (3,461 core → 7,125 required, 3,664 newly supported) |
| `pfe_timing_s` | absent | present |

**Consequence:** the newest of those three is selected for 2027-07-15, so that
one day of the annual warm bank would be built on the *old* candidate pool —
without the every-edge support augmentation — while the other 366 days use the
new one. Nothing in the artifact records the difference, and the warm states
chained from it inherit it silently.

The general form matters more than the single day: across a run of several
days, **nothing prevents a mid-run source edit from splitting the bank into
pre- and post-change halves** with no way to tell them apart afterward.

Two ways out, either sufficient: delete or move aside the three stale archives
before launching (cheapest, fixes today's instance), or add the candidate/PFE
source digests to what `validate_demand_archive` requires so a stale archive
fails to match and is rebuilt (fixes the class). The `candidate_provenance`
block already present in new archives is the natural thing to require.

## 7. The documented launch command no longer works

`validation/annual_warm_readiness_v1.json` still reads
`status: "ready_for_full_population"` and

```
commands.execute_full = "... --plan-key b89e4a5e617ebe11ba6d448a6ccb5a1afa9af13eaac29ace3ed152292105a542 ..."
```

while the live plan's `content_key` is `0ce86bca…98d10`. `_require_plan_key`
raises on mismatch, so this fails closed immediately — no harm, just a wasted
attempt. This is deliberate (`test_readiness_manifest_is_superseded_by_the_new_calendar_source`
and `test_superseded_readiness_source_binding_cannot_appear_current` assert the
supersession), so it is a stale-documentation papercut rather than a defect. The
right root is already initialized and clean:

```
runs/annual-warm-2027/0ce86bca…98d10/
  work_units : 104,685    states: {pending: 104,685}    attempts>0: 0
```

Six superseded roots (~34 MB each) also sit under `runs/annual-warm-2027/`;
harmless, but they are the kind of thing that makes a later "which root am I
in?" mistake possible.

---

## Verified healthy — don't redo these

- **v16 is adopted.** `monthly_warm_state_v16_adoption.json`: `monthly_command_default: "warm"`,
  `cache_miss: provisional_bootstrap`, `unusable_warm_attempt: cold_fallback`,
  3 entries into `runs/warm-state`. The residual campaign that blocked
  everything in the 08-03 review is closed.
- **Focused check passes:** `test_annual_warm_plan / _store / _population /
  populate_annual_warming / _readiness` — **40 passed** in 103 s.
- **Preflight passes** on the live plan: SUMO 1.27.1, TraCI API complete,
  localhost bind, network fingerprint match, 3 approved workers.
- **The chaining exactness evidence is real** — at depth 1. All ten prefix-
  evidence sections byte-equal, `value_max_absolute_delta_s = 0.0`,
  `warm_metrics_equal_direct_cold = true`.
- **Storage dedup works as designed** — route blobs shared across a chain,
  ~121 KB marginal per checkpoint.

## Recommended order

| # | Action | Cost | Why first |
|---|---|---|---|
| 1 | Remove or invalidate the 3 stale 2027-07-15 archives (§6) | one command | Otherwise one day of the bank is silently built on the pre-change candidate pool |
| 2 | Run **one full 96-link day-chain** and check exactness at links 2/48/96 plus `expanded_bytes` vs link index | ~0.09 % of the run | Closes the only unmeasured correctness assumption *and* produces the missing timing number |
| 3 | Add the `exists()` short-circuit in `_store_member` before compressing | one check | Measured ~23 h serial / ~7.7 h at 3 workers, zero semantic change |
| 4 | Decide on batching a chain into one SUMO process | design | ~95× fewer launches and route parses; the dominant term in the 4-day estimate |
| 5 | Bind archive validation to candidate/PFE provenance, not just dates (§6) | small | Fixes the class, not just today's instance |
| 6 | Confirm 3-day archive size empirically before relying on the 49 GB margin | one build | Failure mode is an abort near the end of a multi-day run |
| 7 | Re-freeze the held-out v6 manifest against the live tree, or record why it stays disabled | — | Orthogonal to the run, but currently silently off |
| 8 | Update or retire `annual_warm_readiness_v1.json`'s command and status | one line | It documents a command that cannot run |

**Bottom line:** the subsystem is careful, fail-closed and genuinely well
evidenced — but all of its evidence is at chain depth 1, and the run it is about
to authorize is chain depth 96 across 104,685 units and several days of compute.
Items 1–3 cost a few hours between them and remove a concrete provenance defect,
the largest correctness unknown, and a measured day of pure waste. I would not
start the full population before items 1 and 2.
