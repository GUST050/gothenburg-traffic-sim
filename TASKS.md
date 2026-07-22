# Tasks

## ACTIVE_GOAL

Design and freeze a stronger v3 held-out case set for the speed-stage-B/day-library work. The cases must show true objective spread greater than 300 seconds, preserve the existing validation gates, and must not merge or warm the horizon until Sol High has reviewed the evidence.

## ACTIVE_TASK

### LUNA-V3-01 — Audit the frozen v3 design and evidence boundary

Owner: Luna High  
Status: DONE

Audit the existing v3 artifacts without regenerating or changing them. Verify
that the manifest was bound before outcomes, that its selected edges are
pilot-backed and disjoint from v1/v2, and that the literal ACTIVE_GOAL
requirement of true objective spread greater than 300 seconds is satisfied at
the intended case level rather than inferred only from an aggregate median.

Acceptance criteria:

- Confirm the manifest/selection binding, case count, schedule count, pilot
  provenance, release identity, source fingerprints, and v1/v2 edge
  disjointness.
- Produce a compact per-ranking-case table in `AGENT_NOTES.md` containing
  eligibility, `objective_spread_s`, discriminating status, practical-winner
  recall, and failed hard-gate counts.
- State explicitly whether every intended discriminating case exceeds the
  frozen 300-second practical-equivalence threshold; do not substitute the
  median spread or discriminating fraction for this check.
- Confirm that the original recall, regret, and failure-recall thresholds are
  unchanged and that the v3-only discrimination checks are additive and
  backward-compatible with v1/v2 manifests.
- Verify the hashes named by
  `validation/strong_v3_failure_analysis.json` against the immutable campaign
  files and confirm that no passing gate record was emitted.
- Run only the focused, non-SUMO checks listed below, record exact results in
  `AGENT_NOTES.md`, and stop for Sol High review.

Files to start with:

- `validation/monthly_proxy_manifest_v3.json`
- `validation/heldout_v3_selection.json`
- `runs/closure-proxy-validation/strong-v3-2026-07-22/outcomes.json`
- `runs/closure-proxy-validation/strong-v3-2026-07-22/report.json`
- `validation/strong_v3_failure_analysis.json`
- `tools/freeze_heldout_v3.py`
- `tools/heldout_v3_selection.py`
- `traffic_sim/simulation/proxy_validation.py`
- `tests/test_heldout_v3_freeze.py`
- `tests/test_proxy_validation.py`

Focused tests and commands:

```bash
python3 -m pytest -q tests/test_heldout_v3_freeze.py
python3 -m pytest -q tests/test_proxy_validation.py
shasum -a 256 runs/closure-proxy-validation/strong-v3-2026-07-22/outcomes.json runs/closure-proxy-validation/strong-v3-2026-07-22/report.json
git diff --check -- tools/freeze_heldout_v3.py tools/heldout_v3_selection.py traffic_sim/simulation/proxy_validation.py tests/test_heldout_v3_freeze.py tests/test_proxy_validation.py validation/monthly_proxy_manifest_v3.json validation/heldout_v3_selection.json validation/strong_v3_failure_analysis.json
```

Do not run `run_monthly_proxy_validation.py`, regenerate the manifest, refresh
stored cases, edit production code, merge stage B, or warm any horizon. Stop
and escalate if a stored hash differs, the literal >300-second requirement is
not met, or any existing gate was weakened.

## TASK_LIST

1. **DONE — LUNA-V3-01:** audit the immutable v3 design, per-case spread,
   provenance, hashes, and backward-compatible gate semantics.
2. **DONE — SOL-V3-02:** review Luna's evidence table and decide whether
   the literal ACTIVE_GOAL is met. A failed or ambiguous case-level check is
   not repairable by editing the already-observed v3 manifest.
3. **PENDING — SOL-V3-03:** record the final v3 disposition: discrimination
   evidence accepted or rejected, release gate failed, no passing gate record,
   and stage B remains unmerged/unwarmed.
4. **PENDING, SEPARATE GOAL ONLY — V4-01:** if Sol accepts the diagnosed
   `stratified_shortlist_v3` policy for further validation, design and freeze a
   fresh untouched v4 campaign with a new policy/source identity. Do not start
   this under the current v3 goal.

## DONE

Completed tasks go here.

## BLOCKED

- Stage-B merge and all horizon warming are blocked until Sol High completes
  SOL-V3-02 and explicitly authorizes the next step.
- The post-hoc replay on v3 outcomes is development evidence only and cannot
  open a release gate.
