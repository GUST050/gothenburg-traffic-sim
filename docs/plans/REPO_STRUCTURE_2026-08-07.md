# Repo structure — finish the migration that already started

**Date:** 2026-08-07 · **Status:** PLANNED, NOT STARTED — blocked until the
annual warming finishes. Delete this file once executed and reconciled.

---

## 0. The blocker, first, because it decides everything

The annual warming is running. **48 files are bound inputs of the plan key**,
and `_verify_plan_source_seal()` re-checks at every demand-group boundary.
Worse, `demand_source_paths()` **globs** two directories:

```
demand/*.py              (9 files)
traffic_sim/demand/*.py  (8 files)
```

so adding *or removing* a file there changes the fingerprint even if nothing
else is touched. Moving a bound file, or any file in those globs, aborts the
run — and per `WARMING_PLAN` §1 that discards **the bank already built**, not
just the run. At the time of writing that is ~26,000 units.

**Nothing in section 3 may start before the warming is complete.** Section 2 is
safe now.

## 1. The finding that reframes the job

This is **not** a greenfield restructure. The target structure already exists:
`traffic_sim/` holds 56 modules in `core/`, `demand/`, `intake/`, `ops/`,
`simulation/`. A migration into it was started and **stalled halfway**, leaving
12 compatibility shims in the repo root:

```
candidate_cache.py       -> traffic_sim.demand.cache            1 importer
closure_metrics.py       -> traffic_sim.simulation.metrics      2
network_audit.py         -> traffic_sim.simulation.network_audit 1
pfe.py                   -> traffic_sim.demand.pfe              8
pfe_kernel.py            -> traffic_sim.demand.pfe_kernel       2
pipeline_fingerprint.py  -> traffic_sim.core.fingerprint        1
release_registry.py      -> traffic_sim.ops.releases            1
runs.py                  -> traffic_sim.ops.runs                3
sensor_registry.py       -> traffic_sim.intake.sensors          1
study_contracts.py       -> traffic_sim.core.contracts          2
sumo_network_metadata.py -> traffic_sim.simulation.metadata     1
sumo_runtime.py          -> traffic_sim.simulation.runtime      0   <- dead
```

Each is six lines: a docstring and a `sys.modules[__name__] = _implementation`
rebind. So the job is **finishing a migration**, which is far lower risk than
inventing a layout: the destination modules are already the real ones, already
imported directly by most of the tree, and already the paths the bound-source
inventory names.

The research consensus (src/ layout, `pyproject.toml`, tests mirroring the
package) mostly describes where this repo is already heading. The one deviation
worth keeping is deliberate: the pipeline entry points
(`build_data.py`, `build_candidates.py`, `build_sumo_demand.py`,
`build_sumo_net.py`, `run_scenario.py`, `assignment_priors.py`,
`prior_flows.py`, `observability.py`) stay at the root, because they are named
in `Makefile`, `README.md`, `CLAUDE.md`, the demand build spec and **the bound
source inventory itself**. Moving them buys tidiness and costs provenance.

## 2. Safe to do NOW (no bound source, no glob)

- `agent-backup-before-sol-luna/AGENTS.old.md` — a single stale backup of a
  file whose content is preserved in git history (`4e4659c`). Delete the
  working-tree copy; history keeps it recoverable.
- This plan file.

**`docs/` needs nothing.** It is already grouped into `plans/`, `reviews/` and
`history/`, with only `README.md` and the current `OPEN_ISSUES` at the top
level — which is where a live handoff belongs. An earlier draft of this section
claimed docs was flat and needed grouping; that was wrong, checked and
corrected before it became work.

## 3. After the warming — the migration itself

Order matters: each step must leave the tree green.

**Step 1 — remove the dead shim.** `sumo_runtime.py` has zero importers.
Delete it alone, run the suite, commit. This is the smallest possible proof
that the mechanism works.

**Step 2 — rewrite importers, one shim at a time**, cheapest first (1 importer,
then 2, then `runs.py` at 3, then `pfe.py` at 8). For each: rewrite the import
sites to the real module, delete the shim, run the focused suites, commit.
Eleven small commits, not one large one — the incremental rule from the
refactoring literature, and it keeps `git bisect` useful.

**Step 3 — `pfe.py` last and deliberately.** Eight importers including
`tests/test_pfe.py`, and the name `pfe` also appears as a LABEL in the bound
source inventory (`_FIXED_SOURCES["pfe"] = "traffic_sim/demand/pfe.py"`). The
label must not change; only the shim goes. Verify
`STARTUP_SOURCE_HASHES == demand_source_fingerprints(root)` still holds after,
which `tests/test_demand_provenance.py` now pins.

**Step 4 — regenerate the plan key.** Every step above touches a bound source
or a globbed directory, so the annual plan and preflight must be rewritten
before any further warming. Expect the key to move; do not copy it anywhere.

**Step 5 — `pyproject.toml`.** There is none today. Adding one is orthogonal to
the migration and should be its own change, after it.

## 4. Explicitly NOT in scope

- **`sumo/` (22 GB) and `runs/` (21 GB)** — untracked, and both are live inputs
  and outputs of the running population. `runs/demand-*` is read by
  `find_demand_archives()`; the populator prunes archives itself as chains
  complete. Do not hand-clean either.
- **The versioned seal families** — 16 `tools/freeze_monthly_warm_state_v*.py`,
  15 matching test modules, 7 `freeze_heldout_v*.py`. They are 157 of the 160
  test failures and they grow with every campaign, but retiring a superseded
  seal is a **design decision about evidence**, not a tidying question. It has
  its own entry in `OPEN_ISSUES` §8 and must not be resolved by deletion here.
- **`validation/` (171 MB, 112 tracked files)** — frozen evidence. Same reason.

## 5. What "done" looks like

```
no compatibility shims in the repo root
root .py = pipeline entry points + explore.py only
every import names its real module
STARTUP_SOURCE_HASHES == demand_source_fingerprints(root)
test suite no worse than its recorded baseline (160 failed / 3790 passed,
  all seal drift)
annual plan and preflight regenerated and verifying
```
