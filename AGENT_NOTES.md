# Agent Notes

## Latest status

Sol High planning completed. `LUNA-V3-01` is complete: the already frozen
and executed v3 campaign was audited read-only and is awaiting Sol High review.

## Latest changes

- Audit evidence and focused test results recorded below; no implementation
  code or frozen campaign artifact changed.

## Tests

- `tests/test_heldout_v3_freeze.py`: 19 passed.
- `tests/test_proxy_validation.py`: 27 passed.
- Campaign hash verification and scoped `git diff --check`: clean.

## Blockers

- Stage B must not be merged and no demand horizon may be warmed before Sol
  High reviews Luna's v3 audit.
- The v3 campaign has observed outcomes and is immutable. Any new validation
  after a policy correction requires a fresh untouched versioned campaign.

## Next step

Sol High reviews the evidence below and decides the v3 disposition. Stage B
and horizon warming remain blocked.

## LUNA-V3-01 audit — 2026-07-22

The frozen v3 manifest is bound before outcomes: `frozen_before_outcomes` is
`true`, `frozen_at` is `2026-07-22T15:20:07Z`, and the manifest content key
`b7b81a7a5f25709556239d0636edd4a876bb5ee0a0506a10567fc6bf441aeb3c` matches
the selection record, outcomes, and report. It contains 13 cases and 143
exhaustive schedules. The selection record contains 13 distinct edges; all
five `expected_discriminating` cases are `pilot_probe` backed with matching
demand-build provenance. The 13 v3 edges are disjoint from the 12 v1 and 12
v2 edges. Manifest release identity and report validated identity match,
including the 18 source fingerprints and demand/policy/shortlist identity.

Per-ranking-case evidence (`eligibility` is eligible/total schedules;
`hard-fail` is the count with one or more failed SUMO hard gates; `disc` is
literal `objective_spread_s > 300`):

| case | eligibility | objective_spread_s | disc | practical-winner recall | hard-fail |
|---|---:|---:|:---:|:---:|---:|
| v3-daytype-4h-e | 12/15 | 364.75 | yes | 1 | 3 |
| v3-daytype-8h-a | 13/15 | 2243.66 | yes | 1 | 2 |
| v3-daytype-8h-b | 14/15 | 640.27 | yes | 1 | 1 |
| v3-daytype-8h-c | 14/15 | 465.64 | yes | 1 | 1 |
| v3-daytype-8h-d | 5/15 | 2384.98 | yes | 0 | 10 |
| v3-primary-far-weekday-4h | 5/9 | 2881.31 | yes | 1 | 4 |
| v3-residential-far-weekday-4h | 8/9 | 850.00 | yes | 1 | 1 |
| v3-residential-near-weekday-8h | 9/9 | 2.06 | no | 1 | 0 |
| v3-secondary-far-mixed-40h | 2/5 | 0.14 | no | 1 | 3 |
| v3-unclassified-medium-weekend-4h | 9/9 | 219.57 | no | 1 | 0 |

All five intended discriminating cases exceed the frozen 300-second
practical-equivalence threshold (the smallest is 364.75 s). This is a
case-level result, not an inference from the 552.955 s median spread. Three
additional ranking cases also exceed 300 s; the two ranking cases below 300 s
are 2.06 s and 219.57 s. The three `failure_only` cases have 0 eligible
schedules and no objective spread, so they are not ranking cases.

The original thresholds are unchanged from v2: practical equivalence 300 s,
practical-winner recall 0.90, p90 normalized regret 0.10, and failure
disqualification recall 0.60. The v3-only checks are additive:
minimum discriminating-case fraction 0.40 and discriminating practical-winner
recall 0.90. v1 and v2 manifests still validate unchanged.

The recorded hashes match exactly:

- outcomes: `435d5112cc08a8320eb3c32cfe745dbc41fd0c7f97dbb18c7698510584eff912`
- report: `7bf1a46e43545d5e957e3e67f6f454b0f139bd0a36955d5b9efb67e33fda8fdd`

The v3 report is `gate_status: fail` with UI/global-best exposure disabled;
`gate_record_for(...)` returns `None`, so no passing gate record was emitted.
Its failed checks are practical-winner recall on discriminating cases
(`0.857143 < 0.90`), failure-disqualification recall (`0.45303 < 0.60`), and
p90 normalized regret (`0.408975 > 0.10`). The post-hoc replay remains
development-only and cannot change this disposition.

Focused checks completed: `python3 -m pytest -q
tests/test_heldout_v3_freeze.py` — 19 passed; `python3 -m pytest -q
tests/test_proxy_validation.py` — 27 passed; the mandated `shasum -a 256`
matched the recorded hashes; scoped `git diff --check` — clean. No SUMO,
manifest regeneration, stored-case refresh, production-code edit, stage-B
merge, or horizon warming was performed.

Status: audit complete; stop for Sol High review. Stage B and horizon warming
remain blocked.

## Permanent decisions

- The existing strong-v3 campaign is failed release evidence, not a passing
  gate: its post-hoc shortlist replay is development-only and must never be
  promoted as held-out evidence.
- No passing gate record may be synthesized from v3. If the corrected
  shortlist policy proceeds, it must use a fresh v4 manifest and untouched
  outcomes under a new policy/source identity.

## Sol High review

REVIEW_STATUS: APPROVED

Final v3 disposition:
- v3 discriminating evidence: accepted
- v3 release gate: failed
- passing gate record: none emitted
- Stage B merge: blocked
- horizon warming: blocked
- fresh v4 campaign: required only as a separate next goal
