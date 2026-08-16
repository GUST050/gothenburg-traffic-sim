# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Main. The 2026-08-13 direction-split plan has been
  reviewed against measured evidence. Findings only — no source
  implementation, no policy activation, no SUMO run.`
- Summary: `The plan's artifact audit reproduces exactly (regenerated
  sumo/direction_split.json: median |q50-0.5| 0.0070, max 0.0340, median
  q10-q90 width 0.1070). Three measured results change what should be built.
  (1) The tournament winner is neither of the plan's two allowed options: an
  UNCONDITIONAL hour-of-day curve with no street features beats 50/50 by
  +5.4/+6.6/+4.9% on leave-city-out (all), leave-city-out (domain) and
  leave-station-out, while every LightGBM variant is 5-9% WORSE than 50/50 on
  all three. Shrinkage lambda tells why: curve 0.93-0.98, LightGBM 0.21-0.44.
  (2) Direction decomposes into LEVEL and SHAPE and only shape transfers -
  the curve is worse than 50/50 at sensor 107's published annual D-factor
  (2.91 pp vs 2.31 pp), while carrying the real within-day tide (weekday
  peak-to-peak 0.099). A single logit offset (+0.1166) composes them: exact
  52.3/47.7 anchor, amplitude preserved. (3) The nominal 80% interval has
  47.0% measured out-of-sample coverage; honest width is 0.193, not 0.099 -
  and since rows are ~8-day means, 47% is an UPPER bound. At sensor 107's
  AM peak the deployed q50 moves 4 vehicles vs 50/50; the validated curve
  moves 37; the honest band is +/-100.`
- Files changed: `Documentation and two analysis tools only:
  docs/reviews/DIRSPLIT_PLAN_RESEARCH_REVIEW_2026-08-16.md (new);
  tools/research_direction_split_evidence.py and
  tools/research_direction_sum_constraint.py (new, tracked-artifact only);
  current TASKS.md and AGENT_NOTES.md blocks. No pipeline, demand, PFE, web
  or test code touched; no model retrained.`
- Checks: `python3 -m tools.research_direction_split_evidence runs end to end
  and reproduces every number in the review. Regenerated split file matches
  the plan's quoted audit numbers to the digit, confirming both the plan's
  audit and this regeneration. Direction mapping verified from
  network.geojson geometry: 60786979_3575001205_0 bearing 352.1 deg = N =
  toward-centre, so the catalogue's N row maps to the toward-centre edge.`
- Decisions and evidence: `No decision taken - review is advisory. RECOMMENDED:
  level-local x shape-pooled with the honest 0.193 interval. The sum-constraint
  ("let entropy choose") alternative was measured and REJECTED same-day, which
  reversed this review's first recommendation. Mechanism: pfe.py appends groups
  to bounds_items, whose correction multiplies EVERY member route by one shared
  factor, so a sum constraint carries no information about the split and the
  seed ratio passes through untouched - verified numerically, "sum only" equals
  n_A/(n_A+n_B) exactly. A 24k-pair gravity/stochastic-multipath probe of the
  pool at 107 then gives implied splits from 0.230 (plain shortest path) to
  0.581, a 35 pp range on the sigma knob alone, against a physical range of
  0.44-0.60 and a measured 0.523. The obvious repair - sum at level 1 plus a
  two-sided level-2 band - is a disguised point estimate: the small IPF seed
  pushes both carriageways to their LOWER bounds, so the answer is
  lo_A/(lo_A+lo_B) regardless of pool. Within one PFE solve there is no way to
  represent an unknown split; that belongs across demand variants, which the
  q-variant architecture already provides. Gate S remains unmeasured; running
  it on the DEPLOYED q-artifacts would understate sensitivity by construction,
  since they span half the honest width.`
- Blockers or risks: `Gate S still requires SUMO and is untouched. The
  tournament used the tracked aggregated table, so it cannot measure true
  day-level variance - that is the plan's dataset-v2 point and it stands; it
  only makes the 47% coverage figure an upper bound. The measured-sum option
  needs a double-count audit for routes touching both carriageways.`
- Suggested next action: `Decide between the two recommended designs, then
  implement Fas 0A with the verified N<->toward-centre mapping recorded
  alongside the raw 3400/3100 values. Independently of any gate, either widen
  q10/q90 to the measured 0.193 or relabel them stress_only - they feed the
  map's confidence number today.`
- Actor notes: `Research combined a full code audit, an empirical tournament
  on tracked data, and FHWA/Van Zuylen-Willumsen primary sources. No existing
  evidence was edited, no external data downloaded, no policy activated and
  no runtime gate weakened. sumo/direction_split.json is gitignored and was
  regenerated only to audit it.`
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
