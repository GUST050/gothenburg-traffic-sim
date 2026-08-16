# Repository agent guide

## Goal and authority

Work from the user's latest request to a verified result. Roles such as
planner, implementer, researcher and reviewer describe work, not identities or
permission classes.

Use these sources in order:

1. The user's latest request and system/tool safety rules.
2. This file and the nearest domain `AGENTS.md`.
3. `ARCHITECTURE.md` for technical contracts.
4. The marked current blocks in `TASKS.md` and `AGENT_NOTES.md`.
5. `IMPROVEMENT_PLAN.md` for longer-term priorities.

Everything outside the marked current blocks is historical unless a living
document explicitly says otherwise. For the expanded collaboration and
handoff conventions, read `docs/ai/COLLABORATION_GUIDE.md` only when needed.

## Start here

For non-trivial work:

1. Read the current blocks in `TASKS.md` and `AGENT_NOTES.md`.
2. Run `git status --short --branch` and preserve unrelated changes.
3. Read `docs/architecture/OVERVIEW.md`, then only the relevant section of
   `ARCHITECTURE.md`.
4. Before editing a domain, read its nearest `AGENTS.md`.
5. Make the smallest coherent change and run the narrowest useful check.
6. Run `make check` before handoff; run `make test` for broad or risky changes.
7. For substantial work, refresh the marked current blocks truthfully.

Do not stop because an old owner, model name, state or handoff names somebody
else. Stop only for a real blocker, safety boundary or material user choice.

## Repository map

- `traffic_sim/`: canonical shared implementation.
- `traffic_sim/demand/`: PFE, route support, caches and provenance.
- `traffic_sim/simulation/`: SUMO runtime, closure search and evidence logic.
- `dirsplit/`: trained direction-allocation model and its gates.
- `demand/`: demand orchestration and domain contracts.
- `signals/`: signal studies and closure combinations.
- `web/`: static Leaflet client; `serve.py` is the local API entry point.
- `tests/`: contract, unit, integration and frozen-evidence tests.
- `validation/`: immutable or append-only release/campaign evidence.
- `tools/`: diagnostics, benchmarks, freezes and bounded maintenance tools.
- `docs/`: architecture map, decisions, plans, reviews and history.

Root Python files are stable command paths. Some are bound into frozen
evidence, so keep their paths and CLI contracts unless an explicit migration
updates every consumer and provenance check.

## Commands

```text
make test-fast       focused deterministic checks
make test-demand     demand/PFE checks
make test-dirsplit   direction-split checks
make test-simulation simulation and scenario checks
make test-web        server/API checks
make lint            critical Python static checks
make repo-hygiene    large-file and artifact policy
make check           lint + hygiene + focused tests
make test            complete pytest suite
```

Run commands from the repository root. Do not claim success without reporting
the exact commands and results. Browser-visible changes also need a real
browser smoke test when the environment supports it.

## Product and evidence invariants

- Measured sensor counts are hard evidence. Missing is not zero.
- Do not weaken validation, provenance, exactness, recall, regret, health,
  release or publication gates merely to make a result pass.
- Diagnostic replay is not release evidence unless the release contract says
  so and the user requested promotion.
- Preserve immutable validation artifacts. Add a new version or supersession
  record instead of rewriting historical evidence.
- Before expensive SUMO or evidence-producing runs, bind inputs and outputs,
  avoid clobbering earlier evidence and state whether the run is diagnostic or
  release evidence.
- Keep secrets out of source, logs and documentation.

## Scope and safety

Read-only inspection, scoped edits and focused tests are normal implementation
steps. Ask before destructive actions, external publication not requested by
the user, spending money, contacting third parties or materially expanding
scope. Never reset or discard unrelated worktree changes.

Prefer one canonical implementation over compatibility shims. Keep modules
cohesive, APIs explicit and imports directed toward `traffic_sim/`. Treat a
production file over 1,000 lines as a review trigger, not an automatic rewrite:
extract one tested responsibility at a time.

Each coordination marker documented in
`docs/ai/COLLABORATION_GUIDE.md` must occur exactly once in its owning file.
