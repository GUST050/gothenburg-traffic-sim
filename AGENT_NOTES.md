# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `PRE-WARMING-CLEANUP-AND-VALIDATION` / `READY_TO_PLAN`.
- Summary: the stage B warming alarm is withdrawn. Six LOSO draws under
  identical code span 0.929-1.537, and the pre-B baseline of 0.971 falls
  inside that range -- the apparent regression was a low draw compared
  against a high draw of the same distribution. Warming is unblocked by
  anything raised here; it needs a fresh plan key and, ideally, v10 first.
- READ FIRST: `docs/OPEN_ISSUES_2026-08-06.md`. Every open item, each marked
  MEASURED / DOCUMENTED / OPEN, with where its evidence lives.
- The finding that outlives this task: a SINGLE LOSO run cannot compare two
  pipeline versions. Draw spread under identical code is 0.608 (SD 0.236),
  the same order as differences previously attributed to code changes. Any
  such claim needs several draws per version; n=3 per arm can never reach
  significance (min two-sided p = 0.100), n=4 is the floor.
  Evidence: `validation/loso_draw_variance_v1.json`.
- Landed this session: C1 closure-integrity fix (lost access is an impact,
  not a broken run) plus the denied-departure hole it exposed; the
  direction model extended to all six sensors as a CEILING only, never a
  floor; a silent target-halving bug in `build_targets`; stage B merged;
  474 lines of retired coverage machinery removed; project map cut from 22
  root documents to 9 and ~50 GB of disposable run output to 19 GB.
- Checks: demand 100% GEH<5 and 0 infeasible on all three variants; 3,687
  passing tests. 158 failures are frozen-contract seal drift (32 of 35
  artifacts), down from 259 at session start -- they are the seals refusing
  to certify old evidence against changed code, not breakage.
- Next, in order:
  1. `tools/plan_annual_warming.py --write` -- the key is stale again.
  2. Freeze and run v10. v9 failed one check of seven
     (`ranking_case_fraction` 0.4 against 0.5); its cause was C1, now fixed,
     so the same selection rule should pass. `global_best_claim_allowed`
     stays False until it does.
  3. Measure link-flow observability for the five unmeasured carriageways.
     It decides whether the direction ceiling should exist at all, and it
     was never measured.
- Not blocking, low priority: six pre-B draws (`6e5763e^`) would turn the
  stage B result into a formal test. A worktree has none of the gitignored
  inputs -- copy `sumo/net.net.xml` in and verify the build before spending
  six runs on it.
<!-- CURRENT_HANDOFF_END -->

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
