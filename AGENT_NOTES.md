# Agent Notes

Only the single marked `CURRENT_HANDOFF` block is current coordination context.
All other entries are preserved history and do not assign roles or restrict
which model may continue. See `AGENTS.md`.

<!-- CURRENT_HANDOFF_START -->
## CURRENT_HANDOFF

- Focus and status: `Repository cleanup and structure, at the user's direct
  request (2026-08-15). DONE. The DIRSPLIT-UNCERTAINTY-V2 task below is
  UNCHANGED and still the next piece of product work — this cleanup touched no
  demand, calibration, closure or direction-split behaviour.`
- Summary: `Finished the stalled traffic_sim/ migration that
  docs/plans/REPO_STRUCTURE_2026-08-07.md specified: rewrote every import site
  and deleted the 12 root compatibility shims; deleted volume_priors.py (dead,
  a rejected approach with no importer or test); collected the five unbound
  signal-study modules into signals/; moved benchmark_seed_workers.py to
  tools/. Root .py went 45 -> 26. Compressed one 36.9 MB pure-archive evidence
  artifact to 3.2 MB (validation/ 64 MB -> 31 MB), verified byte-exact against
  the sha256 already bound in its members.json.`
- Files changed: `Deleted 12 root shims + volume_priors.py +
  docs/plans/REPO_STRUCTURE_2026-08-07.md; moved 5 signal modules into
  signals/ and benchmark_seed_workers.py into tools/; rewrote imports in 21
  tests and 3 tools; serve.py (subprocess paths + one import) is the only
  evidence-bound source touched, and only because correctness required it;
  README.md, ARCHITECTURE.md, docs/README.md updated; validation/README.md
  added.`
- Checks: `Full suite run and compared against the recorded baseline. The
  demand source seal was verified byte-identical to HEAD across all 28 bound
  sources, so the demand fingerprint and the annual plan key did NOT move —
  REPO_STRUCTURE step 4 (regenerate the plan key) turned out to be
  unnecessary. Both signals/ invocation styles verified; every Makefile target
  path verified to resolve.`
- Decisions and evidence: `Two rules drove every choice. (1) A root path is an
  interface when something immutable records it: the campaign runners and
  run_scenario.py have their paths inside frozen validation/ artifacts and
  tools/freeze_*.py, so they stayed; signals/ had zero such bindings, so it
  moved. (2) Do not touch a sealed demand source for cosmetic reasons — a
  comment fix in assignment_priors.py was written and then reverted for exactly
  this reason, which is why the fingerprint held. Frozen evidence was
  compressed, never deleted; the user was asked first and chose compression.`
- Blockers or risks: `None introduced. The pre-existing seal-drift failures are
  untouched and remain a design decision about evidence (OPEN_ISSUES §8), not a
  tidying question.`
- Suggested next action: `Resume DIRSPLIT-UNCERTAINTY-V2 Fas 0A exactly as
  TASKS.md ACTIVE_TASK states it — provenance-bind sensor 107's yearly
  directional reference and pin legacy behaviour. Nothing about that task
  changed. If any future work wants to move a root file, check first for its
  path inside validation/ and tools/freeze_*.py; ARCHITECTURE.md records the
  rule.`
- Actor notes: `Cleanup only. No calibration, closure, demand or direction-split
  behaviour was changed; no evidence was deleted, regenerated or weakened; no
  gate was relaxed. The user explicitly chose "make the evidence smaller"
  over deleting it.`
<!-- CURRENT_HANDOFF_END -->

## Superseded handoff — 2026-08-13 dirsplit scope correction

Preserved verbatim; superseded as the *current* block by the 2026-08-15
cleanup above. The task it describes is still open and is specified in
`TASKS.md` ACTIVE_TASK and
`docs/plans/DIRSPLIT_UNCERTAINTY_AND_CLOSURE_USE_PLAN_2026-08-13.md`.

- Summary: `The end-to-end audit found that today's q10/q50/q90 are learned
  from station-hour means, not raw day-level variation; weekend/off-hour
  predictions lack training support; applicability only covers static
  features; and global marginal quantiles are not coherent daily scenarios.
  q50 has only a 0.0008 pooled MAE advantage over 50/50 after shrinkage and is
  worse in three of four held-out domain cities. Review then established that
  only sensor 107's split directly creates two Level-1 targets; five opposite
  directions are surrenderable Level-2/3 evidence. The plan now starts with
  107's local 52/48 period anchor and a bounded matched-seed sensitivity study.
  Gates S/M/P prevent speculative scenario/monthly/warm/API/UI work.`
- Files changed: `Documentation only:
  docs/plans/DIRSPLIT_UNCERTAINTY_AND_CLOSURE_USE_PLAN_2026-08-13.md;
  IMPROVEMENT_PLAN.md pointer; current TASKS.md and AGENT_NOTES.md blocks.`
- Checks: `git diff --check clean; plan has balanced code fences and all Gate
  S/M/P, Exit A/C, Gren B/D and sensor-107 contract terms are present; current
  marker counts are exactly one start/end pair. No code tests were required
  because source behavior is unchanged.`
- Decisions and evidence: `50/50 winning does not imply zero variance, so exit
  requires both Gate M=BASELINE and Gate S=NO. The other combinations lead to
  no ensemble, a residual-only prototype, or a conditional-model prototype as
  documented. Sensor 107's annual D-factor is a local period anchor, not 96
  directed measurements. Existing closure v5 evidence remains unchanged.`
- Blockers or risks: `The raw, citable source/period semantics for 107's
  3,400/3,100 values must be bound before treating them as product evidence.
  Gate S must be preregistered before rerunning SUMO. Raw Norwegian day-block
  availability is measured later in Fas 1. No scenario cap or risk policy is
  needed unless Gate S/M open that branch.`
- Suggested next action: `Fas 0A only: add the provenance-bound 107 reference,
  anchor its period mean, and regression-test that the five directional
  Level-1 sensors remain unchanged. Then freeze Fas 0B; do not build schemas or
  product integration.`
- Actor notes: `Research used primary statistical, scenario-generation,
  traffic-monitoring and microsimulation sources. No existing evidence was
  edited, external data downloaded, policy activated or runtime gate weakened.`

## History

Everything before 2026-08-06 lives in `docs/history/AGENT_NOTES_history.md`
(14,681 lines). Preserved context only — per
`AGENTS.md`, nothing outside the marked block above is current.
