# Frozen-evidence retirement — census, diagnosis and proposed design

**2026-08-27 · DESIGN ONLY. Nothing in section 4 is implemented.**

**Follow-up:** the stale concurrency assertion identified in §2.3 was fixed
separately on 2026-08-27. The retirement proposal and its open policy choices
remain unimplemented.

Continues `docs/OPEN_ISSUES_2026-08-06.md` §8 ("Test suite: 156 failures, and
why they are not fixed"), which ended on the open question this document
answers: **when is a superseded vN seal retired?**

Everything in sections 1–3 is measured on this worktree at HEAD `be7b6bd`.
Section 4 onward is a proposal. Every measurement command is named so the
numbers can be re-derived rather than trusted.

---

## 0. The one-line diagnosis

The project already decided how retirement works. It is written into the
artifacts themselves — `validation/monthly_warm_state_manifest_v16.json`:

```json
"lifecycle_rules": {
  "drift_is_the_retirement_mechanism":
    "a superseded contract's fingerprints drift and its load fails closed;
     that drift is never repaired or re-synced",
  "immutable_history_pins":
    "... predecessor TEST files are never pinned, because a retiring suite
     must stay free to describe its own supersession"
}
```

Drift *is* retirement. A retiring suite is *supposed* to describe its own
supersession. What is missing is only the **assertion side and the CI side**:

* every `test_*_vN_freeze.py` still asserts that its seal **loads**, so a
  correctly-retired seal reports as a failure rather than as a retirement;
* nothing in the repository distinguishes "this seal is retired and its drift
  is expected" from "this seal is active and its drift is a real problem", so
  CI cannot tell program correctness from expired evidence.

The defect is therefore **not** the seals, the hashes or the discipline. It is
that the declared retirement mechanism was never given a way to say so out
loud. This document proposes completing it.

---

## 1. Current census (measured, static, no test run required)

### 1.1 Artifacts that bind source hashes

Scanned `validation/**.json` for the three hash-binding blocks
(`source_fingerprints`, `frozen_fingerprints`, `input_fingerprints`), then
re-hashed every bound path against the working tree.

| quantity | 2026-08-06 (§8) | 2026-08-27 (this run) |
|---|---|---|
| artifacts binding source hashes | 43 | **50** |
| drifted | 42 | **47** |
| clean | 1 | **3** |

The three clean artifacts are `heldout_v7_selection.json`,
`heldout_v8_selection.json`, `heldout_v9_selection.json`.

Split by *why* they drift:

| category | artifacts |
|---|---|
| a tracked source file changed (true code drift) | 31 |
| only unresolvable inputs (generated / untracked paths) | 16 |
| clean | 3 |

Growth of +7 bound artifacts and +5 drifted in three weeks is the monotonic
accumulation §8 predicted, now measured rather than forecast.

### 1.2 What drives the drift

Number of artifacts each changed source file invalidates:

```
23  traffic_sim/core/closure_calendar.py       23  traffic_sim/simulation/monthly_search.py
20  traffic_sim/simulation/metrics.py          20  traffic_sim/simulation/monthly_sumo.py
20  traffic_sim/simulation/monthly_warm_state.py
19  run_scenario.py                            18  traffic_sim/simulation/warm_state_cache.py
18  traffic_sim/simulation/warm_state_boundary.py
17  run_monthly_warm_state_validation.py       16  suggest_closure_time.py
16  traffic_sim/core/contracts.py              16  traffic_sim/simulation/envelope.py
16  traffic_sim/simulation/finalist_decision.py
13  tests/test_monthly_sumo.py                 10  tests/test_monthly_warm_state.py
```

`closure_calendar.py` has overtaken `monthly_search.py` as equal-largest
driver since §8. Note that **test files are bound too** — `test_monthly_sumo.py`
alone invalidates 13 artifacts. That matters for section 4.3: a seal can be
broken by editing a test, which is the cheapest possible edit to make.

### 1.3 How far the failure count overstates the number of causes

Full run of the 142 test files that precede and include `tests/test_serve.py`
in collection order (`python3 -m pytest tests/ -q --ignore=<the 29 later
files>`), 11m42s:

```
157 failed, 4103 passed, 29 skipped, 2 warnings, 5 errors
```

162 failure lines. Reducing every `E <ExceptionType>: <message>` line to its
distinct text:

```
27 distinct root error messages
```

The concentration is extreme. The largest single block, measured inside
`tests/test_monthly_warm_state_v2_freeze.py` alone (42 failed, 54 passed):

| failures | cause |
|---|---|
| 32 | `AssertionError: Regex pattern did not match` — a `pytest.raises(match=...)` that now catches a *different, earlier* contract error |
| 3 | that earlier error raised directly: `split diagnostics field set is wrong` |
| 4 | `prefix evidence requires the meso prefix accumulator` |
| 1 | `legacy prefix-evidence schema 'monthly_prefix_evidence_v3'` |
| 2 | missing `sumo/net.net.xml`, missing `runs/demand-…` archive |

**35 of those 42 failures are one field-set change.** See §2.2.

A second cascade, measured directly:
`tests/test_monthly_warm_state_residual.py` + `…_residual_v2.py` = 11
failures from exactly **2** distinct causes (one `DiagnosticError: tracked
network differs from v9`, one missing `sumo/net.net.xml`). §8's warning that
"the count overstates the number of distinct causes" is confirmed, and the
overstatement factor is roughly **6×** (162 lines / 27 causes).

### 1.4 A category §8 did not separate: tests that cannot pass in a clean checkout

This worktree is a fresh `git worktree` — it has no `sumo/` at all and an
almost-empty `runs/`. That is exactly the state of **GitHub CI**, which
checks out and runs `pytest tests/` with nothing generated.

Of the 162 failure lines, these are missing generated inputs, not evidence
drift and not code defects:

```
45  FileNotFoundError            (sumo/net.net.xml, sumo/…, runs/releases/…, runs/demand-…)
 7  SystemExit                   (no sumo/direction_split.json — "run `make demand`")
 3  CanonicalBindingError        (canonical archive is not a real directory)
 2  ValueError                   (--direction-stress-variants requires a q10/q90 contract)
```

≈ **57 of 162 (35%)** are of this kind.

> **Honest caveat, and it matters for comparing to §8.** §8's 156 was measured
> in the primary worktree, where `sumo/` and `runs/` are populated by real
> campaigns. My 157 is a *different set* of the same size: it loses whatever
> §8 counted that needs live artifacts and gains these 57. The two numbers
> should not be treated as the same 156 tests. What is common to both, and
> what section 4 is about, is the seal-drift core.

### 1.5 The versioned-seal family

```
20  tests/test_*_v*_freeze.py modules      26  tools/freeze_*.py
24  validation/*_manifest_v*.json          (monthly_warm_state v1–v16, monthly_proxy v2,v4–v10)
```

126 of the 162 failure lines live in `test_*_freeze.py` modules.

---

## 2. Expired historical evidence vs. genuine present-day violation

### 2.1 The split, with counts

| bucket | lines | basis |
|---|---|---|
| **A. Expired historical evidence** (retired-in-fact seals still asserting they load) | ~100 | 126 in `*_freeze.py` modules, minus the ~26 of those that are actually 1.4-category missing inputs |
| **B. Cannot pass in a clean checkout** (missing generated artifacts) | ~57 | §1.4, exception classes counted directly |
| **C. Genuine present-day mismatch between an assertion and shipping behaviour** | **1 confirmed** | §2.3 |
| **D. Not individually classified** | remainder | stated as unclassified rather than guessed |

I did not classify every one of the 162 to the individual test. Buckets A and
B are counted by exception class and module, which is reliable at the level of
"how big is each pile" and not reliable at the level of "which test is in
which pile". I am not asserting more precision than that.

### 2.2 `WarmStateContractError: split diagnostics field set is wrong: missing=['corrected_post_metrics', 'restore_correction']`

**Verdict: seal drift — expired historical evidence. Not a present-day
mismatch.** Three independent confirmations:

1. **The current validator and the current emitter agree.**
   `traffic_sim/simulation/monthly_warm_state.py:120` defines
   `SPLIT_DIAGNOSTIC_FIELDS` as 12 fields including both names.
   `traffic_sim/simulation/monthly_sumo.py:1875-1888` builds
   `split_diagnostics={...}` with exactly those 12, `corrected_post_metrics`
   and `restore_correction` among them. Production emits what production
   validates.

2. **The current, unversioned contract suite passes.**
   `python3 -m pytest tests/test_monthly_warm_state.py -q` → **59 passed**.
   Its fixtures (lines 131-132, 516-517) include both fields.

3. **Only the frozen suite fails, and its fixture is v2-shaped.**
   `tests/test_monthly_warm_state_v2_freeze.py::_diag()` composes 10 fields —
   `bounded_sections()` plus explicit keys — and never adds the two the
   snapshot/restore-correction work later introduced. Its own code comment in
   `SPLIT_DIAGNOSTIC_FIELDS` records the reason for the change ("v3 published
   one entry per airborne vehicle, which made the canonical payload grow with
   traffic").

So the v2 suite is testing a data shape the product deliberately stopped
producing. That is the seal doing its job. The 35 failures it produces are one
retired contract, counted 35 times.

### 2.3 The one confirmed present-day violation

`tests/test_independent_daily.py::test_server_routes_independent_search_to_exact_exhaustive_mode`
— fails **in isolation** (`1 failed in 0.07s`), so it is not order- or
environment-dependent.

`serve.monthly_screening_cli_args()` returns, for
`interday_policy == "independent_daily_reset_v1"`:

```
--screening-mode independent-exhaustive
--daily-workers <N> --seed-workers <N> --max-active-sumo-slots <N>   <-- test does not expect these
--daily-unit-budget … --daily-unit-total-cap … --independent-exhaustive-candidate-cap …
```

The test's expected list omits the three concurrency flags. **Production is
right and the test is stale**: the live campaign on this machine (PID 68201)
was launched with `--daily-workers 8 --seed-workers 1
--max-active-sumo-slots 8`, so the shipped path is the one with the flags.

This is a stale *assertion*, not a stale *seal* — no hashes are involved. It
would stay red under any retirement scheme and should be fixed on its own
merits by updating the expectation to the shipped argument list. **I did not
change it** (out of scope for a design-only task, and it is a behavioural
assertion someone should confirm deliberately).

---

## 3. Why CI is red, stated precisely

Three unrelated things are currently summed into one red X:

1. **Retired evidence asserting it is still current** (bucket A) — expected,
   healthy, and should be *asserted*, not merely tolerated.
2. **Tests that require a local `make demand` / campaign output** (bucket B) —
   structurally impossible in CI. CI has never been able to pass these and
   never will without shipping the artifacts.
3. **Real defects** (bucket C) — currently exactly one confirmed, and it is
   invisible inside the other 161 lines.

A gate that cannot separate these is not a strict gate. It is an unread gate.
That is the actual cost: the one real failure in §2.3 has been sitting in the
noise.

---

## 4. Proposed design — completing the declared retirement mechanism

### 4.1 Principles it must not break

* Historical SHA-256 values are **never** edited, re-synced or regenerated.
* No test is deleted, skipped or xfailed to obtain green.
* No drift, provenance or release gate is loosened.
* A retired seal is still **executed and still asserted** — as STALE.
* Retirement can never be used to escape a failure: retiring requires a
  successor that is itself active and green (§4.4).

### 4.2 One added field, reusing the vocabulary already in the artifacts

Manifests already carry `status` (`frozen_unapproved_unexecuted`,
`frozen_pre_outcome_design`), `frozen_at`, `campaign_version`,
`inherited_from` and `lifecycle_rules`. Add exactly one block:

```json
"lifecycle": {
  "state": "retired",
  "superseded_by": "validation/monthly_warm_state_manifest_v16.json",
  "retired_at": "2026-08-27",
  "reason": "v16 corrects the localized formatter semantic; v2's split-diagnostics
             shape predates corrected_post_metrics/restore_correction",
  "history_digest": "<sha256 of this artifact's own source_fingerprints block>"
}
```

Three states only:

| state | meaning | what the test asserts |
|---|---|---|
| `active` | certifies current code | live sources **reproduce** the seal |
| `retired` | superseded by a named successor | live sources **must differ**, and the seal's own bytes are unchanged |
| `broken` | active, but drifted, with no successor | **fails.** This is the real red signal |

`broken` is not a state anyone writes by hand — it is what the census
*computes* for an artifact that says `active` and has drifted. It is the bar,
and it is the bar that must never be lowered.

`history_digest` is the load-bearing part for the owner's "SHAs must be
preserved" requirement: it is a hash **of the hash block**, so any later edit
to a historical fingerprint changes it and the retired suite fails on
immutability. Retirement makes the history *more* tamper-evident, not less.

### 4.3 What a retired seal's test module asserts

A shared helper, `traffic_sim/evidence/seal_lifecycle.py`, exposing one
function the versioned suites call:

```python
def assert_retired(manifest_path):
    """Three assertions, all positive, none skipped."""
    # 1. IMMUTABILITY — the historical hashes are byte-unchanged
    #    sha256(canonical(manifest["source_fingerprints"])) == lifecycle["history_digest"]
    # 2. STALENESS — the seal must refuse to load against current sources
    #    with pytest.raises(<the family's contract error>, match="drift|differs|invalid")
    # 3. SUCCESSION — superseded_by exists, its lifecycle.state == "active",
    #    and its bound-source set is a superset of this one, or declares a
    #    narrowing with a written reason (see §4.6)
```

This is not a new idiom for this repository. It is the generalisation of two
patterns already here:

* `tests/test_annual_warm_readiness.py::test_readiness_manifest_is_superseded_by_current_sources`
  — already asserts staleness positively, with
  `pytest.raises(ValueError, match="tracked annual plan differs from current sources")`.
* `tests/test_heldout_v6_freeze.py::…::test_freeze_preview_reports_drift_without_rewriting_history`
  — already reports drift without rewriting history.

**The hard part, stated plainly.** A retired module contains two kinds of
test, and they need different treatment:

* *Seal-binding tests* ("these sources reproduce the manifest", "this
  manifest's content_key is X") — these become `assert_retired()`. Clean.
* *Era-behaviour tests* ("a v2-shaped diagnostics dict is rejected with
  message M") — the 35 in §2.2. Today they assert message M; the current code
  raises message M′ *earlier*, because the v2 fixture is no longer a valid
  object at all.

  For these, the proposal is **not** to edit 35 `match=` strings to the new
  message. That would be adjusting tests to match code, which is the thing
  this repository refuses to do. Instead the module declares once, at module
  scope, that its fixtures are era fixtures:

  ```python
  pytestmark = retired_era_fixtures(
      manifest="validation/monthly_warm_state_manifest_v2.json",
      validator="traffic_sim.simulation.monthly_warm_state.validate_split_diagnostics",
      rejected_because="field set is wrong",   # the CURRENT rejection, asserted once
  )
  ```

  `retired_era_fixtures` asserts once, positively, that the era's fixture
  shape is now rejected by the named current validator with the named
  message — the honest historical statement, "this shape is no longer
  accepted" — and converts the module's remaining era-behaviour tests into
  that single covering assertion. **This is the part of the design I am least
  sure about and the owner should decide it** — see §5.

### 4.4 Retirement can never be used to get green

`assert_retired` fails unless the successor exists **and is `active` and
itself passes**. Concretely, for the monthly-warm-state family:

* v1–v15 → `retired`, `superseded_by` the next version, ultimately v16.
* v16 → the only `active` seal.
* **v16 is itself drifted: 15 of its 23 bound sources have changed**
  (`run_scenario.py`, `suggest_closure_time.py`, `closure_calendar.py`,
  `contracts.py`, `envelope.py`, `finalist_decision.py`, `metrics.py`,
  `monthly_search.py`, `monthly_sumo.py`, `monthly_warm_state.py`,
  `warm_state_boundary.py`, `warm_state_cache.py`, plus three test files).

So under this design the family computes to **`broken`**, and the suite goes
red for exactly one reason with exactly one name — which is the point.
Retiring v1–v15 converts ~100 lines of noise into ~15 passing retirement
assertions and **one** honest red flag.

### 4.5 CI: three jobs, two of which must be green

```yaml
jobs:
  correctness:        # MUST be green — this is "can we ship"
    run: pytest tests/ -q -m "not evidence_archive and not requires_local_artifacts"

  evidence-archive:   # MUST also be green — retired seals assert their staleness
    run: pytest tests/ -q -m evidence_archive

  seal-census:        # informational; publishes validation/seal_census.json
    run: python3 tools/seal_census.py --check-no-broken
```

Two markers, applied at module scope, never per-test:

* `evidence_archive` — modules whose manifest declares `lifecycle.state ==
  "retired"`. Still runs, still must pass. Not a skip.
* `requires_local_artifacts` — the §1.4 bucket. These need a decision (§5),
  because the honest options are "skip with a printed reason and a count" or
  "ship the fixtures".

`tools/seal_census.py` is section 1 of this document as a script:
every hash-binding artifact, its declared state, its computed state, its
drifted sources. `--check-no-broken` is the gate. Because the census is
static, it runs in seconds and gives the owner a standing answer to "what is
actually stale right now" without a 13-minute suite.

### 4.6 Successor creation, and the narrowing precedent

A successor is created the normal way — `tools/freeze_<family>_v<N+1>.py`
against real new evidence — with two additions:

1. it writes `lifecycle.state = "active"` and sets its predecessor's
   `lifecycle.state = "retired"` with `superseded_by` pointing at itself;
2. it must bind **only sources that can change the evidence**.

Point 2 is not new discipline either. CLAUDE.md already records it as a
solved problem for the route catalog:

> `catalog_identity_payload` used to hash the whole 31-entry demand source
> inventory; measured, only 6 of those are reachable from
> build_candidates.py's import closure … The cost was real: commit c653b24,
> whose entire purpose was to HARDEN catalog qualification, invalidated the
> adopted catalog by editing three files that cannot change a routed edge.

The same measurement is available here, and §1.2 says it is probably needed:
a manifest that binds `tests/test_monthly_sumo.py` is invalidated by editing
a test — 13 artifacts are in that position. **Measuring the real import
closure of what produced each campaign's evidence, in a subprocess, and
binding that** would be a correction of the same kind, not a weakening. It is
the single change most likely to stop v17 rotting as fast as v16 did.

I have **not** measured those closures. That is implementation work.

### 4.7 Order of work (not started)

1. `tools/seal_census.py` + `validation/seal_census.json`. Read-only,
   reversible, and it makes every later step measurable. Nothing else can be
   argued well without it.
2. `traffic_sim/evidence/seal_lifecycle.py` with `assert_retired`, and its own
   tests, applied to **one** family member first (v1) as a pilot.
3. Decide §5's open questions with the owner.
4. Roll `lifecycle` blocks through monthly_warm_state v1–v15 and
   monthly_proxy v2–v9, one commit per family.
5. CI split, with `--check-no-broken` as the new gate.
6. Only then: decide what to do about the genuinely broken v16.

---

## 5. Trade-offs, risks, and what the owner must decide

### 5.1 Risks in the proposal

**R1 — "retired" becomes a bin for anything inconvenient.** The mitigation is
§4.4 (a successor must exist and be active) plus the census computing
`broken` independently of what the artifact claims. It is a real risk and the
census is the only thing that keeps it honest; it should be treated as a
release-blocking gate, not a report.

**R2 — the era-fixture collapse (§4.3) loses coverage.** Converting 35
era-behaviour assertions into one covering assertion genuinely reduces the
number of things asserted. The counter-argument is that all 35 currently
assert a message the product no longer produces, so their present coverage is
zero — but "zero coverage" and "one assertion" are both worse than the
coverage they had when they were written. This is a real loss and should be
stated as one, not dressed up.

**R3 — retiring 15 seals at once is a large, low-feedback change.** Hence the
one-family pilot in §4.7 step 2.

**R4 — bucket B (§1.4) has no clean answer.** Skipping 57 failures with a
reason is uncomfortably close to the thing this task forbids. It is defensible
only because these tests cannot pass in *any* clean checkout, including CI —
they are testing local build outputs, not code. But it is a judgement call and
it is the owner's to make, not mine.

**R5 — this does not fix v16.** After all the work, the family is still
`broken`. The design converts 100+ lines of noise into one true statement; it
does not make the statement false. Making CI green *for real* requires either
a v17 freeze against a real campaign, or the §4.6 narrowing showing most of
v16's 15 drifted sources cannot affect its evidence.

### 5.2 Decisions I need from the owner

1. **The era-fixture question (§4.3, R2).** Three options, in increasing order
   of how much I would trust them:
   (a) collapse to one covering assertion (my proposal);
   (b) leave era-behaviour tests failing and mark only the seal-binding tests
       retired — honest, but CI stays red and the whole point is lost;
   (c) freeze the era fixtures' *current* rejection as new evidence, i.e. write
       a small `v2_era_rejection` artifact recording that shape and message —
       more work, but it keeps a real assertion per era and creates evidence
       rather than deleting it.
   I lean (c) if there is appetite for the work, (a) if not. I do not think
   (b) is worth doing.

2. **Bucket B policy (R4).** Skip-with-reason-and-count, ship fixtures, or
   accept a permanently red CI job for them?

3. **Is `active` one per family, or one per family *purpose*?** The
   monthly_proxy family (v2, v4–v10) and monthly_warm_state (v1–v16) are
   clearly two families. `heldout_v4…v10` may be a third or may be several.
   I have not audited that and would rather ask than assume.

4. **Does retirement require an executed successor, or does a
   `frozen_unapproved_unexecuted` successor count?** v16's own `status` is
   `frozen_unapproved_unexecuted`. If an unexecuted successor cannot retire
   its predecessor, then nothing in this family can be retired today and step
   6 has to come first.

5. **Should the §2.3 stale assertion be fixed now**, separately from all of
   this? It is one line, unrelated to seals, and it is the only confirmed real
   defect in the 162.

---

## 6. What was measured, and how to re-derive it

| claim | command |
|---|---|
| 50 binding artifacts, 47 drifted, drivers | static scan of `validation/**.json` re-hashing every bound path (seconds, no test run) |
| 157 failed / 5 errors / 27 distinct causes | `pytest tests/ -q --ignore=<29 files after test_serve.py>`, 11m42s |
| 42 failures in the v2 seal, 35 from one field set | `pytest tests/test_monthly_warm_state_v2_freeze.py -q --tb=line` |
| current contract suite green | `pytest tests/test_monthly_warm_state.py -q` → 59 passed |
| §2.3 fails in isolation | `pytest tests/test_independent_daily.py::test_server_routes_independent_search_to_exact_exhaustive_mode -q` → 1 failed in 0.07s |
| v16 drifted 15/23 | static scan of `validation/monthly_warm_state_manifest_v16.json` |

The full test suite (`pytest tests/`) was **not** run: a real 8-hour campaign
(PID 68201) was live on this machine throughout and the suite competes with it
for CPU. The 142-file run above stops immediately after
`tests/test_serve.py`; the 29 files after it in collection order were not
executed, so the totals here are a lower bound on the whole suite.
