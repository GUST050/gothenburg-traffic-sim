# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Branch claude/gate-s-critical-findings-brkzye, after TWO
  review rounds. Round 1: both gates technically wrong, 107 anchor
  disconnected. Round 2: Gate S could still return a FALSE YES by three
  routes, its inertness check was grouped so it could not detect the defect
  it existed for, the 107 anchor reached only one of the two demand
  branches, and Gate M still was not evaluating the deployed model. All
  repaired. The load-bearing new finding is that the q10/q90 stress cases do
  not isolate the direction axis, so Gate S is now blocked on EVIDENCE
  QUALITY as well as egress. Gate S = NOT_RUN, Gate M = INCONCLUSIVE. No
  exit declared.`
- Summary: `Round 1 fixed: the Gate S tool never varied the route file or the
  seed, read a disruption field the product does not emit, ignored its own
  registered closure window, and reduced a private objective; the 107 anchor
  was a helper no product caller invoked; Gate M ran the wrong population and
  the wrong model under code that did not implement its own frozen rule.
  Round 2 fixed the three remaining routes to a FALSE YES — a seed-varying
  ranking key (a broken measurement, now INCONCLUSIVE), an
  always-disqualified candidate opening the gate on a cost the policy never
  reads, and a seed-inertness check grouped per case instead of per (case,
  candidate) — plus a silently substituted closure window, a fail-open
  topology filter, an unverified registered date, and a Gate M report with no
  provenance binding. It also closed the second half of the 107 gap:
  write_counts loaded the unanchored split AND lacked the single-direction
  guard, so it was writing 48% of every measured single-direction count. The
  load-bearing new finding is that q10/q90 do not isolate direction at all,
  which now blocks Gate S on evidence quality independently of egress.`
- Files changed: `tools/measure_direction_decision_sensitivity.py (run path,
  reducer, isolation check, fail-closed guards); dirsplit/evaluate.py
  (deployed population and model, per-station fit, guarded numerics,
  pairwise comparisons, required fold kinds, rule v2, provenance digests);
  demand/intake.py (anchor plumbing + volume weights);
  demand/publication.py (write_counts anchor + the missing
  single-direction guard); build_sumo_demand.py (four call sites);
  tests/test_direction_decision_sensitivity.py, tests/test_dirsplit_gate_m.py,
  tests/test_sensor_107_directional_reference.py,
  tests/test_build_sumo_demand.py, tests/test_opposite_direction.py;
  data/dirsplit/gate_m_report.json (regenerated);
  validation/dirsplit_gate_m_outcome_v{2,3}.json and
  validation/dirsplit_direction_sensitivity_blocker_v{2,3}.json (each
  supersedes the previous). No earlier evidence file was edited.`
- Checks: `Touched suites all green. Gate M reruns clean under
  -W error::RuntimeWarning (no divide-by-zero, overflow or invalid value).
  Both reviewer reproductions were replayed against the new reducer and now
  return INCONCLUSIVE / NO as they should. All 36 Gate S ScenarioSpecs still
  validate through run_scenario's own validator and resolve to three
  DISTINCT route files. On real flows both demand branches now agree at
  52.30% against the published 52.308%.`
- Decisions and evidence: `GATE M = INCONCLUSIVE, not BASELINE. Rule 6 of the
  frozen text says a fold kind that could not be built is INCONCLUSIVE;
  dirsplit/dataset.py aggregates away local_date, so blocked_date yields zero
  folds and the v1 code skipped it and published BASELINE anyway. Under the
  corrected DEPLOYED population (observed weekday-daytime share screen: 81
  stations / 3,665 rows / 162 blocks, not the OSM-oneway-flag 39 stations /
  1,514 rows) and the deployed LightGBM — target-centred kernel, ONE MODEL
  PER HELD-OUT STATION as deployment fits per sensor, n_obs**0.5 evidence
  weight, weekday 06-20 fit, shrinkage fit by nested leave-city-out
  re-centred per inner station — the LightGBM is worse than 50/50 on
  leave-city-out (MAE 0.0648 vs 0.0627, CI95 [+0.000384, +0.003873]) and
  indistinguishable on leave-station-out. That is DIAGNOSTIC only. The claim
  "the deployed LightGBM is 31.6-39.2% worse" is withdrawn: it measured a
  different model on a different population. The per-station correction also
  moved the figure from -4.7% to -3.3%, so even the interim number
  overstated the loss.
  GATE S = NOT_RUN. Recorded while repairing: the deployed ranking key
  (closure_disruption) is demand-side and therefore seed-deterministic BY
  CONSTRUCTION, so the old "between-case spread beats seed noise" ratio on it
  was a tautology; the tool verifies the invariant instead, and a violation
  is now INCONCLUSIVE rather than YES. Round 2 also found the q10/q90
  artifacts confound direction with total volume (max |pair sum - 1| =
  0.297), which is why Gate S cannot produce a meaningful answer even where
  the route files exist.`
- Blockers or risks: `Gate S is blocked TWICE OVER. (1) Evidence quality:
  the q10/q90 artifacts do not isolate direction — measured max
  |pair sum - 1| = 0.297 against a 0.001 tolerance, because predict.py pairs
  s10 with 1-s90. Until they are rebuilt with a pair-sum-preserving
  construction, every Gate S run returns INCONCLUSIVE by design, and that is
  correct: the alternative is an answer that cannot be attributed to
  direction. That rebuild is Fas 2 work and was NOT done. (2) Egress:
  build_candidates.py needs overpass-api.de; the proxy answers 403 to CONNECT
  for it and for geodata.scb.se, re-verified 2026-08-14.
  /root/.ccr/README.md requires reporting such denials rather than retrying,
  so no workaround was attempted. The same denial prevents raw day-level
  Norwegian volumes, which is why Gate M cannot build blocked_date folds.`
- Suggested next action: `Rebuild q10/q90 so each station's directed pair
  sums to 1.0 in every slot — that is the prerequisite for Gate S meaning
  anything, and it is cheap compared with re-running the matrix on artifacts
  that cannot answer the question. Then run the repaired Gate S on a machine
  that already has sumo/calibrated*.rou.xml. Separately, build dataset v2
  (station x date x hour x heading with raw counts and day_block_id) to make
  Gate M decidable; that needs Norwegian volume egress. Do NOT open Fas
  2/3/4: neither gate is decided.`
- Actor notes: `SUMO installed locally and resolved from
  SUMO_HOME=/usr/local/lib/python3.11/dist-packages/sumo. sumo/net.net.xml and
  sumo/direction_split.json were rebuilt from tracked inputs; both are
  gitignored intermediates and were not committed. No existing evidence file
  was edited, no policy activated and no runtime gate weakened. The v1
  outcome and v1 blocker are preserved unchanged and explicitly superseded.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
