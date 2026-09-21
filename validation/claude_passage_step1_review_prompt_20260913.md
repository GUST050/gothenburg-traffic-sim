# Prompt to continue the passage speed plan after Step 1 review

Work on your existing branch `claude/exciting-rubin-1e6k5m`, starting from
commit `c3a5b2eff4583edec700d2a3b720dbe516fe140c`. Read the current Step 0-8
section in `IMPROVEMENT_PLAN.md` and the current blocks in `TASKS.md` and
`AGENT_NOTES.md`. Do not rewrite unrelated history.

The Step 1 approach is accepted, but the commit is not accepted as-is. An
independent local review found two H1 contract defects. Fix both with tests
before doing further optimization:

1. `expand_departure_support_verified` is a public validation bypass. A
   `PassageSystem` is a public dataclass and can be constructed directly, so
   accepting that type does not prove that the public option validation ran.
   Rename this fast path to a private internal helper such as
   `_expand_departure_support_from_system`. Keep the public
   `expand_departure_support(...)` API fully validating arbitrary callers.
   Update only controlled internal callers in automatic passage and the replay
   profiler. Add a regression test that the verified fast-path name is not
   exported as a public API.

2. `profile()` checks that its shared solver cache is outside source evidence,
   but a direct caller of `replay(..., solver_cache_dir=...)` can bypass that
   check and write inside the source tree. Make the replay cache parameter
   internal (for example `_solver_cache_dir`) and validate inside `replay()`
   before creating either output or cache directories. Require it to be outside
   source evidence and, for a supplied shared cache, inside the profile output
   root. Add a direct-call regression test that attempts to place the cache
   below the evidence root and proves that no file or directory was created.

The exact local reviewer patch is available as
`validation/claude_step1_review_fix_20260913.patch` if the user supplies the
local file. Reproduce the two changes directly if that file is unavailable.

Re-run at least:

```sh
python3 -m pytest -q \
  tests/test_profile_passage_replay.py \
  tests/test_trial_dynamic_passage.py \
  tests/test_dynamic_assignment.py \
  tests/test_automatic_passage.py \
  tests/test_passage_solver_checkpoint.py
git diff --check
```

The reviewed local version passed 144 focused tests and 489 tests in the wider
passage/builder/day-library/monthly dependency set. The controlled local A/B is
in `validation/passage_step1_ab_20260913.json`:

- median hot replay: 3.387834 s -> 3.0411445 s, a 0.3466895 s or 10.23% gain;
- median cold replay: 4.975038 s -> 4.653363 s, a 0.321675 s or 6.47% gain;
- every array in the A and B solver `request.npz` files is exactly equal;
- all 18 replay runs have identical selection, route and agent SHA-256 values;
- each three-repeat profile reports solver-cache hits `[false, true, true]`.

Do not treat the different cross-version solver request keys as a regression.
The checkpoint key intentionally binds the source bytes of
`dynamic_assignment.py`; the underlying numerical requests are exact.

After the two H1 repairs are green, commit and push the correction to the same
Claude branch. Then begin only the measurement part of Step 2. Add read-only,
non-semantic profiler output that reports, separately for saved source and
candidate data:

- vehicle count;
- number of unique full edge tuples;
- number of unique endpoint pairs;
- call counts and exclusive wall time for `_route_structure_metrics`,
  `purpose_lengths_km`, `purpose_length_bins`, and `route_od_distance_km`;
- whether route definitions were inline or named;
- the geometry and sensor identity that a future route-facts cache must bind.

Keep these timings and counters outside semantic fingerprints. Add fixture
tests for repeated routes, same endpoints with different interior edges,
inline and named routes, sensor revisits, and changed geometry identity. Do not
implement the Step 2 cache yet and do not claim a production time saving: the
local machine must first run this instrumentation on the frozen q50 evidence.
Do not start a monthly search, general date warming, catalog rebuild,
qualification or adoption. Do not weaken sensor, route, population, solver,
provenance, or byte-identity gates.

Report the new commit SHA, exact tests, files changed, and the one local command
needed to profile the frozen evidence. If the required local artifacts are not
available in your container, say so and stop after delivering the tested
instrumentation; do not fabricate production measurements.
